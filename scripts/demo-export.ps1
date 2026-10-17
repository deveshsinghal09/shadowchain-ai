param([string]$BaseUrl = 'http://127.0.0.1:8000')
. (Join-Path $PSScriptRoot 'api-client.ps1')
$apiHeaders = Get-ShadowChainHeaders
$ErrorActionPreference = 'Stop'
$base = $BaseUrl.TrimEnd('/') + '/api/v1'
$status = Invoke-RestMethod -Headers $apiHeaders "$base/aws/status"
if ($status.export_mode -ne 'local') {
    throw 'This demo requires EXPORT_MODE=local; it never uploads reports to AWS.'
}
$cases = Invoke-RestMethod -Headers $apiHeaders "$base/investigations"
if ($cases.count -eq 0) {
    & (Join-Path $PSScriptRoot 'demo-investigation.ps1') -BaseUrl $BaseUrl | Out-Null
    $cases = Invoke-RestMethod -Headers $apiHeaders "$base/investigations"
}
$caseId = ($cases.investigations | Select-Object -Last 1).investigation_id
if (-not $caseId) { throw 'No synthetic investigation is available for export.' }
$body = @{request_id='demo/phase10/local-export'; investigation_id=$caseId; format='json'} |
    ConvertTo-Json
$report = Invoke-RestMethod -Headers $apiHeaders "$base/reports" -Method Post -ContentType 'application/json' -Body $body
$repeat = Invoke-RestMethod -Headers $apiHeaders "$base/reports" -Method Post -ContentType 'application/json' -Body $body
$stored = Invoke-RestMethod -Headers $apiHeaders "$base/reports/$($report.report_id)"
if ($report.status -ne 'PUBLISHED' -or $report.report_id -ne $repeat.report_id -or
    $report.content_sha256 -ne $repeat.content_sha256 -or
    $stored.content_sha256 -ne $report.content_sha256) {
    throw 'The graph did not retain a repeatable published report.'
}
[PSCustomObject]@{
    ReportId=$report.report_id
    ExportMode=$report.export_mode
    Status=$report.status
    Bytes=$report.byte_count
    SHA256=$report.content_sha256
    Repeatable=$true
    AWSCalls=$false
} | ConvertTo-Json
