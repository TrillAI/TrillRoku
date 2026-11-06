plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val apiBaseUrlFile = rootProject.projectDir.parentFile.resolve("config/api-base-url.txt")
require(apiBaseUrlFile.isFile) { "Missing shared config/api-base-url.txt; see scripts/sync_api_base_url.py." }
val apiBaseUrl = apiBaseUrlFile.readText().trim().trimEnd('/')
require(apiBaseUrl.startsWith("https://")) { "The TrillRoku API origin must use HTTPS." }
val escapedApiBaseUrl = apiBaseUrl.replace("\\", "\\\\").replace("\"", "\\\"")

android {
    namespace = "ai.trillroku.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "ai.trillroku.companion"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
        buildConfigField("String", "TRILLROKU_API_BASE_URL", "\"$escapedApiBaseUrl\"")
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}
