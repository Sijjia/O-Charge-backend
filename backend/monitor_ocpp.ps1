# OCPP Log Monitor для Windows PowerShell
# Использование: .\monitor_ocpp.ps1 [-Station EVI-0011] [-Level INFO] [-Tail 10]

param(
    [string]$Station = "",
    [ValidateSet("DEBUG", "INFO", "WARNING", "ERROR")]
    [string]$Level = "INFO",
    [int]$Tail = 0
)

# Цветовая схема
$Colors = @{
    DEBUG = "Cyan"
    INFO = "Green"
    WARNING = "Yellow"
    ERROR = "Red"
    STATION = "Magenta"
    BOLD = "White"
}

# Уровни логирования
$LogLevels = @{
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
}

$MinLevel = $LogLevels[$Level]

# Пути к файлам логов
$LogPath = "logs\ocpp_debug.log"
$ErrorPath = "logs\ocpp_errors.log"

# Создаем папку logs если её нет
if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}

# Создаем файлы если их нет
if (-not (Test-Path $LogPath)) {
    New-Item -ItemType File -Path $LogPath | Out-Null
}
if (-not (Test-Path $ErrorPath)) {
    New-Item -ItemType File -Path $ErrorPath | Out-Null
}

function Write-ColoredLog {
    param(
        [string]$Line
    )
    
    # Определяем уровень лога
    if ($Line -match ' - (DEBUG|INFO|WARNING|ERROR) - ') {
        $LogLevel = $Matches[1]
        
        # Проверяем уровень
        if ($LogLevels[$LogLevel] -lt $MinLevel) {
            return
        }
        
        # Фильтр по станции
        if ($Station -and $Line -notmatch $Station) {
            return
        }
        
        # Определяем цвет по уровню
        $Color = $Colors[$LogLevel]
        if (-not $Color) {
            $Color = "White"
        }
        
        # Выводим с цветом
        if ($Line -match '🔌|🚨|🔴|🟢|❌|✅|🔍') {
            Write-Host $Line -ForegroundColor $Color
        } elseif ($Line -match 'EVI-\d+|Station [A-Z0-9-]+') {
            Write-Host $Line -ForegroundColor $Colors.STATION
        } else {
            Write-Host $Line -ForegroundColor $Color
        }
    } else {
        Write-Host $Line
    }
}

function Monitor-LogFile {
    param(
        [string]$FilePath,
        [ref]$Position
    )
    
    if (-not (Test-Path $FilePath)) {
        return
    }
    
    try {
        $FileStream = [System.IO.FileStream]::new($FilePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $Reader = [System.IO.StreamReader]::new($FileStream)
        
        # Переходим к последней позиции
        $FileStream.Seek($Position.Value, [System.IO.SeekOrigin]::Begin) | Out-Null
        
        while (-not $Reader.EndOfStream) {
            $Line = $Reader.ReadLine()
            if ($Line) {
                Write-ColoredLog $Line
            }
        }
        
        # Обновляем позицию
        $Position.Value = $FileStream.Position
        
        $Reader.Close()
        $FileStream.Close()
    }
    catch {
        Write-Warning "Ошибка чтения файла $FilePath : $_"
    }
}

# Начальные позиции в файлах
$LogPosition = [ref]0
$ErrorPosition = [ref]0

# Если нужно показать последние строки
if ($Tail -gt 0) {
    Write-Host "=== Последние $Tail строк ===" -ForegroundColor Yellow
    
    if (Test-Path $LogPath) {
        $Lines = Get-Content $LogPath -Tail $Tail
        foreach ($Line in $Lines) {
            Write-ColoredLog $Line
        }
    }
    
    Write-Host ("-" * 50) -ForegroundColor Gray
}

# Устанавливаем позиции на конец файлов для мониторинга только новых записей
if (Test-Path $LogPath) {
    $LogPosition.Value = (Get-Item $LogPath).Length
}
if (Test-Path $ErrorPath) {
    $ErrorPosition.Value = (Get-Item $ErrorPath).Length
}

# Заголовок
Write-Host "=== OCPP Log Monitor ===" -ForegroundColor White
Write-Host "Уровень: $Level" -ForegroundColor $Colors[$Level]
if ($Station) {
    Write-Host "Станция: $Station" -ForegroundColor $Colors.STATION
}
Write-Host "Файлы: $LogPath, $ErrorPath"
Write-Host ("-" * 50) -ForegroundColor Gray
Write-Host "Нажмите Ctrl+C для остановки мониторинга" -ForegroundColor Yellow
Write-Host ""

# Основной цикл мониторинга
try {
    while ($true) {
        # Мониторим основной лог файл
        Monitor-LogFile -FilePath $LogPath -Position $LogPosition
        
        # Мониторим файл ошибок
        Monitor-LogFile -FilePath $ErrorPath -Position $ErrorPosition
        
        # Пауза перед следующей проверкой
        Start-Sleep -Milliseconds 500
    }
}
catch {
    if ($_.Exception.GetType().Name -eq "PipelineStoppedException") {
        Write-Host "`nМониторинг остановлен" -ForegroundColor Yellow
    } else {
        Write-Error "Ошибка мониторинга: $_"
    }
}

Write-Host "Мониторинг завершен" -ForegroundColor Green 