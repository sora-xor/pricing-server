@Library('jenkins-library') _

def pipeline = new org.docker.AppPipeline(steps: this,
    dockerImageName: 'sora2/pricing-server',
    dockerRegistryCred: 'bot-sora2-rw',
    dockerImageTags: ['deps-update' : 'latest'],
    secretScannerExclusion: '.*docker-compose.yml',
    gitUpdateSubmodule: true)
pipeline.runPipeline()
