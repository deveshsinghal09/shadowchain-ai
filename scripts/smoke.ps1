param([string]$BaseUrl = 'http://127.0.0.1:8000')
$ErrorActionPreference = 'Stop'
foreach ($endpoint in @('health', 'ready')) {
    $response = Invoke-WebRequest "$BaseUrl/api/v1/$endpoint" -UseBasicParsing
    if ($response.StatusCode -ne 200) { throw "$endpoint failed" }
    Write-Host "$endpoint $($response.StatusCode) $($response.Content)"
}
