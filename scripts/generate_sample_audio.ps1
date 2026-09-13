<#
.SYNOPSIS
    Generates backend/tests/fixtures/audio/platform_sync.wav from the platform_sync
    transcript fixture, using the Windows built-in speech synthesiser.

.DESCRIPTION
    Test audio for M4 transcription. Produced independently of the system under
    test: using Gemini's own text-to-speech to create audio for Gemini
    transcription would be circular.

    Two voices exist on a stock Windows install (David, Zira). Speakers are
    assigned by gender and distinguished further by speaking rate. Names are not
    spoken as labels; like a real recording, who is speaking must be inferred
    from the conversation itself.

    The WAV is git-ignored (a few MB); re-run this script to recreate it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\generate_sample_audio.ps1
#>

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$root = Split-Path -Parent $PSScriptRoot
$transcript = Join-Path $root "backend\tests\fixtures\transcripts\platform_sync.txt"
$outDir = Join-Path $root "backend\tests\fixtures\audio"
$outFile = Join-Path $outDir "platform_sync.wav"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# speaker -> voice, rate (-10..10)
$voices = @{
    "Priya"   = @("Microsoft Zira Desktop", 0)
    "Meera"   = @("Microsoft Zira Desktop", 2)
    "Arjun"   = @("Microsoft David Desktop", 0)
    "Karthik" = @("Microsoft David Desktop", -2)
}

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile($outFile, $format)

$builder = New-Object System.Speech.Synthesis.PromptBuilder
$first = $true
foreach ($line in Get-Content -Path $transcript -Encoding UTF8) {
    if ($line -notmatch '^\s*(\w+):\s*(.+)$') { continue }
    $speaker = $Matches[1]; $text = $Matches[2]
    # Priya is never addressed by name in the fixture; let her introduce herself
    # so the transcriber has the same information a human listener would.
    if ($first) { $text = "Hi, this is Priya. " + $text; $first = $false }
    $voice, $rate = $voices[$speaker]
    $style = New-Object System.Speech.Synthesis.PromptStyle
    $style.Rate = if ($rate -gt 0) { [System.Speech.Synthesis.PromptRate]::Fast } elseif ($rate -lt 0) { [System.Speech.Synthesis.PromptRate]::Slow } else { [System.Speech.Synthesis.PromptRate]::Medium }
    $builder.StartVoice($voice)
    $builder.StartStyle($style)
    $builder.AppendText($text)
    $builder.EndStyle()
    $builder.EndVoice()
    $builder.AppendBreak([TimeSpan]::FromMilliseconds(450))
}

$synth.Speak($builder)
$synth.Dispose()

$info = Get-Item $outFile
"Wrote {0} ({1:N0} bytes)" -f $info.FullName, $info.Length
