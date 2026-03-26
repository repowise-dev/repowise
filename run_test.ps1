# Load .env and run repowise init --test-run on interview-coach
$envFile = "C:\Users\ragha\Desktop\repowise\.env"
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line -match "^([^=]+)=(.*)$") {
        $key = $matches[1].Trim()
        $val = $matches[2].Trim().Trim('"').Trim("'")
        if ($key -and $val) {
            [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
}

Write-Host "GEMINI_API_KEY loaded: $($env:GEMINI_API_KEY.Substring(0,10))..."
Write-Host "OPENAI_API_KEY loaded: $($env:OPENAI_API_KEY.Substring(0,10))..."

# Force UTF-8 output so filenames with special chars don't crash structlog/Rich on Windows
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Set-Location "C:\Users\ragha\Desktop\repowise"
uv run repowise init "C:\Users\ragha\Desktop\interview-coach" `
    --provider gemini `
    --model gemini-3.1-flash-lite-preview `
    --test-run `
    --yes
