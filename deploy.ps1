$ErrorActionPreference = "Stop"

$ProjectId = (gcloud config get-value project 2>$null).Trim()
if (-not $ProjectId) {
    Write-Error "Could not determine gcloud project. Please run 'gcloud auth login' and 'gcloud config set project <PROJECT_ID>'."
    exit 1
}

$BackendImage = "gcr.io/$ProjectId/birdsong-backend"

Write-Host "Building Backend Docker image using Cloud Build..."
gcloud builds submit --config cloudbuild.yaml .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Deploying Backend to Cloud Run..."
gcloud run deploy birdsong-backend `
  --image $BackendImage `
  --region us-central1 `
  --allow-unauthenticated `
  --set-secrets="GOOGLE_API_KEY=GOOGLE_API_KEY:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,EBIRD_API_KEY=EBIRD_API_KEY:latest,XENO_CANTO_API_KEY=XENO_CANTO_API_KEY:latest"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Fetching Backend URL..."
$BackendUrl = (gcloud run services describe birdsong-backend --region us-central1 --format="value(status.url)").Trim()
Write-Host "Backend is live at: $BackendUrl"

Write-Host "Updating frontend/index.html to use the deployed Backend URL..."
$IndexContent = Get-Content frontend/index.html -Raw
$IndexContent = $IndexContent -replace "const API_BASE = .*;", "const API_BASE = '$BackendUrl';"
Set-Content -Path frontend/index.html -Value $IndexContent -NoNewline

Write-Host "Deploying Frontend to Cloud Run..."
gcloud run deploy birdsong-frontend `
  --source frontend/ `
  --region us-central1 `
  --allow-unauthenticated
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Deployment complete! Your frontend and backend are both live."
