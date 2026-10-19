# Starts llama-server for the translator app (OpenAI-compatible API at http://<host>:<port>/v1).
#
#   Same PC as the app:   .\tools\start-llama-server.ps1
#   Serve other PCs too:  .\tools\start-llama-server.ps1 -BindHost 0.0.0.0 -ApiKey "<long random string>"
#
# -BindHost 0.0.0.0 accepts connections from your network; always pair it with -ApiKey (llama-server
# has no other access control) and set the same key as LLAMA_API_KEY in the app's .env.
param(
    [string]$Model = "typhoon-translate1.5-4b.Q8_0.gguf",
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8080,
    [string]$ApiKey = ""
)

$root = Split-Path -Parent $PSScriptRoot
$serverArgs = @(
    "-m", "$root\models\$Model",
    "-ngl", "99", "-c", "8192", "-np", "1",
    "--host", $BindHost, "--port", $Port
)
if ($ApiKey) {
    $serverArgs += @("--api-key", $ApiKey)
} elseif ($BindHost -ne "127.0.0.1") {
    Write-Warning "Listening on $BindHost without -ApiKey: anyone on the network can use this server."
}
& "$root\tools\llama.cpp\llama-server.exe" @serverArgs
