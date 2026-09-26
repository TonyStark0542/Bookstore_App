pipeline {
    agent any // Executes on any available Jenkins node containing the Docker CLI plugin

    environment {
        // Bind your protected vault strings safely down to temporary script memory variables
        GEMINI_KEY_SECRET = credentials('GEMINI_API_KEY')
    }

    stages {
        // ==============================================================================
        // STAGE 1: PRE-FLIGHT ENVIRONMENT VALIDATION
        // ==============================================================================
        stage('Pre-Flight Validation') {
            steps {
                echo '🚀 [STAGE 1] Triggering environment sanity validations...'
                // Inject the secret key temporarily into shell memory to clear the script script check
                withEnv(["GEMINI_API_KEY=${GEMINI_KEY_SECRET}"]) {
                    sh 'chmod +x deploy_check.sh'
                    sh './deploy_check.sh' // If this script exits with code 1, Jenkins aborts the pipeline instantly
                }
            }
        }

        // ==============================================================================
        // STAGE 2: COMPILE OPTIMIZED MULTI-STAGE IMAGES
        // ==============================================================================
        stage('Parallel Docker Build') {
            steps {
                echo '🏗️ [STAGE 2] Building multi-stage production container layers...'
                // We run build commands sequentially or in parallel layers to keep execution clean
                sh 'docker compose build --no-cache'
            }
        }

        // ==============================================================================
        // STAGE 3: CREDENTIAL MASKING & ENV DEPLOYMENT LAYOUT
        // ==============================================================================
        stage('Secure Pass Configuration') {
            steps {
                echo '🔐 [STAGE 3] Interpolating masked vault parameters into runtime workspace...'
                // Write a fresh .env file using shell interpolation. 
                // Jenkins automatically replaces strings like *** in the console output window loggers.
                sh """
                    echo "GEMINI_API_KEY=${GEMINI_KEY_SECRET}" > .env
                """
            }
        }

        // ==============================================================================
        // STAGE 4: CLUSTER LAUNCH & DATABASE SEEDING
        // ==============================================================================
        stage('Infrastructure Launch') {
            steps {
                echo '🚀 [STAGE 4] Provisioning application containers and seeding database blocks...'
                             
                // 1. Launch the new three-tier container cluster in detached background mode
                sh 'docker compose up -d'
                
                // 2. Give the MongoDB container socket 5 seconds to warm up before data transmission
                sh 'sleep 5'
                
                // 3. Force a database restore to seed your bookstore collections immediately
                sh 'docker exec -i mongodb-backend mongorestore --archive=/backup/db_backup.archive --gzip'
                
                echo '🎉 Deployment cycle successful. App is live at http://localhost:8080'
            }
        }
    }

    post {
        always {
            echo '🧹 Cleaning workspace build logs...'
            // Sanitizes the active directory so secret text strings aren't lingering in the clear text system filesystem
            cleanWs() // Project 2 requirement: automated workspace cleanups
        }
        failure {
            echo '❌ Pipeline failed during validation check or launch gates. Review stack traces.'
        }
    }
}