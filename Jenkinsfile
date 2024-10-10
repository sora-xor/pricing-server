@Library('jenkins-library') _

def pipeline = new org.docker.AppPipeline(steps: this,
    dockerImageName: 'sora2/pricing-server',
    dockerRegistryCred: 'bot-sora2-rw',
    secretScannerExclusion: '.*docker-compose.yml',
    dockerImageTags: ['master':'latest', 'add-vxor-dex':'test-101024'],
    deepSecretScannerExclusion: ["web.py", "alembic.ini"],
    gitUpdateSubmodule: true)
pipeline.runPipeline()
