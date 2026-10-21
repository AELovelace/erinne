<#
.SYNOPSIS
  Overnight fine-tune of the Erinne model on a Windows machine with an RTX 3080.

.DESCRIPTION
  1. trains a LoRA on training\out\sft.jsonl       (~2-3 h for ~9k examples)
  2. merges it into LFM2.5-2.6B
  3. converts to GGUF and quantises to Q4_K_M       -> training\out\erinne-YYYY-MM-DD-q4_k_m.gguf

  Every step logs to training\out\overnight.log. If a step fails the script
  stops; re-running skips steps whose output already exists.

  One-time setup (PowerShell, in C:\Scripts\erinne or wherever this repo is):
    py -3.12 -m venv .venv-train            # 3.12 or 3.14 both work
    .venv-train\Scripts\pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu128
    .venv-train\Scripts\pip install -r training\requirements.txt
    git clone --depth 1 --branch b11756 https://github.com/ggml-org/llama.cpp C:\llama.cpp\src
  llama-quantize.exe comes with the llama.cpp release zip (C:\llama.cpp\bin).

.EXAMPLE
  .\training\run_overnight.ps1
#>
param(
    [string]$Data = "training\out\sft.jsonl",
    [string]$Python = ".venv-train\Scripts\python.exe",
    [string]$LlamaSrc = "C:\llama.cpp\src",
    [string]$Quantize = "C:\llama.cpp\bin\llama-quantize.exe",
    # Out of GPU memory? -MaxLen 3072 (drops ~1% of examples).
    [int]$MaxLen = 4096,
    [double]$Epochs = 2
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot)
$out = "training\out"
$log = "$out\overnight.log"
$name = "erinne-$(Get-Date -Format yyyy-MM-dd)"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HUB_DISABLE_TELEMETRY = "1"

function Step([string]$title, [scriptblock]$body) {
    $stamp = Get-Date -Format "HH:mm:ss"
    "[$stamp] === $title" | Tee-Object -FilePath $log -Append
    # Progress bars go to stderr; don't let PowerShell treat that as an error.
    $ErrorActionPreference = "Continue"
    & $body 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "$title failed (exit $LASTEXITCODE); see $log" }
}

foreach ($p in @($Data, $Python, "$LlamaSrc\convert_hf_to_gguf.py", $Quantize)) {
    if (-not (Test-Path $p)) { throw "Missing: $p (see the setup notes at the top of this script)" }
}
"[$(Get-Date -Format HH:mm:ss)] Starting: $((Get-Content $Data | Measure-Object -Line).Lines) examples" | Tee-Object -FilePath $log -Append

if (-not (Test-Path "$out\erinne-lora\adapter_model.safetensors")) {
    Step "Train LoRA" { & $Python training\train_lora.py --data $Data --out "$out\erinne-lora" --max-len $MaxLen --epochs $Epochs }
}
if (-not (Test-Path "$out\erinne-merged\config.json")) {
    Step "Merge" { & $Python training\merge_lora.py --adapter "$out\erinne-lora" --out "$out\erinne-merged" }
}
if (-not (Test-Path "$out\$name-bf16.gguf")) {
    Step "Convert to GGUF" { & $Python "$LlamaSrc\convert_hf_to_gguf.py" "$out\erinne-merged" --outtype bf16 --outfile "$out\$name-bf16.gguf" }
}
Step "Quantise Q4_K_M" { & $Quantize "$out\$name-bf16.gguf" "$out\$name-q4_k_m.gguf" Q4_K_M }
$hash = (Get-FileHash "$out\$name-q4_k_m.gguf" -Algorithm SHA256).Hash
"[$(Get-Date -Format HH:mm:ss)] Done: $out\$name-q4_k_m.gguf  sha256 $hash" | Tee-Object -FilePath $log -Append
"Next: evaluate it against the stock model (training\README.md, step 5)." | Tee-Object -FilePath $log -Append
