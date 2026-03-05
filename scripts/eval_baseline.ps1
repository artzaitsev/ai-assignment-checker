$ErrorActionPreference = "Stop"

$API = "http://localhost:8000"
$INPUT_DIR = "D:\study\Answers"
$OUT_CSV = "D:\study\ai-assignment-checker\scripts\eval_baseline_report.csv"

$candidate_id = "cand_01KJX4G2SBN4MBB6R63EP9TWGG"
$assignment_id = "asg_01KJX4J24EJVDYBP0A9XZXPE75"

$files = Get-ChildItem -Path $INPUT_DIR -File | Where-Object {
    $_.Extension -in ".docx",".pdf",".txt",".md"
} | Sort-Object Name

$rows = @()

function Try-ParseJson($s) {
    try { return ($s | ConvertFrom-Json) } catch { return $null }
}

function SafeSnippet($s) {
    $t = [string]$s
    if ([string]::IsNullOrEmpty($t)) { return "" }
    $n = [Math]::Min(120, $t.Length)
    return $t.Substring(0, $n)
}

foreach ($f in $files) {
    Write-Host "Processing $($f.Name)..."

    $err = ""
    $debug_run = ""
    $submission_id = ""
    $normalized_key = ""
    $chars = 0
    $useful_chars = 0
    $words = 0
    $is_empty = $true

    # 1) upload
    $upload = curl.exe -s -X POST "$API/submissions/file" `
      -F "file=@$($f.FullName)" `
      -F "candidate_public_id=$candidate_id" `
      -F "assignment_public_id=$assignment_id"

    $u = Try-ParseJson $upload
    if ($u -eq $null -or -not $u.submission_id) {
        $err = "upload_failed"
        $debug_run = SafeSnippet $upload
    } else {
        $submission_id = [string]$u.submission_id
    }

    # 2) run pipeline (try twice)
    if ($err -eq "") {
        for ($i = 0; $i -lt 2; $i++) {
try {
    $resp = Invoke-RestMethod -Method Post -Uri "$API/internal/test/run-pipeline" `
      -ContentType "application/json" `
      -Body (@{ submission_id = $submission_id } | ConvertTo-Json -Compress)

    # Для совместимости с вашим кодом ниже
    $r = $resp
    $debug_run = "ok"
}
catch {
    $r = $null
    $debug_run = SafeSnippet $_.Exception.Message
}
            if ($r -ne $null -and $r.artifacts -and $r.artifacts.normalized) {
                $normalized_key = [string]$r.artifacts.normalized
                break
            }
            Start-Sleep -Milliseconds 200
        }
        if ($normalized_key -eq "") {
            $err = "run_pipeline_no_normalized"
        }
    }

    # 3) download normalized
    if ($err -eq "") {
        $norm_text = curl.exe -s "$API/internal/artifacts/download?key=$normalized_key"
        $debug_run = SafeSnippet $norm_text
        $n = Try-ParseJson $norm_text

        if ($n -eq $null -or -not $n.content_markdown) {
            $err = "download_or_parse_failed"
        } else {
            $content = [string]$n.content_markdown
            $chars = $content.Length

            $useful = $content -replace "(?s)^# normalized\s+source:.*?\n", ""
            $useful_chars = $useful.Trim().Length
            $words = ($useful -split "\s+" | Where-Object { $_ -ne "" }).Count
            $is_empty = ($useful_chars -lt 30)
        }
    }

    $rows += [pscustomobject]@{
        file_name        = $f.Name
        ext              = $f.Extension
        submission_id    = $submission_id
        normalized_key   = $normalized_key
        chars_total      = $chars
        chars_useful     = $useful_chars
        words_useful     = $words
        normalized_empty = $is_empty
        error            = $err
        debug_run        = $debug_run
    }
}

$rows | Export-Csv -Path $OUT_CSV -NoTypeInformation -Encoding UTF8
Write-Host "DONE. Report saved to: $OUT_CSV"

