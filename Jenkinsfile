@Library('jenkins-library') _

def pipeline = new org.docker.AppPipeline(steps: this,
    dockerImageName: 'sora2/pricing-server',
    dockerRegistryCred: 'bot-sora2-rw',
    secretScannerExclusion: '.*docker-compose.yml',
    dockerImageTags: ['master':'latest', 'PR-86': 'fix'],
    gitUpdateSubmodule: true)
pipeline.runPipeline()
