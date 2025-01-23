@Library('jenkins-library') _

def pipeline = new org.docker.AppPipeline(steps: this,
    dockerImageName: 'sora2/pricing-server',
    dockerRegistryCred: 'bot-sora2-rw',
    secretScannerExclusion: '.*docker-compose.yml\$|.*docker-compose-external.yml\$',
    secretScannerExclusion: '.*Cargo.toml\$|.*README.md\$'
    dockerImageTags: ['master':'latest'],
    deepSecretScannerExclusion: ["web.py", "alembic.ini"],
    gitUpdateSubmodule: true)
pipeline.runPipeline()
