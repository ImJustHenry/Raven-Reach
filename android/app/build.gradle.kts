plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "app.ravenreach.phone"
    compileSdk = 35
    defaultConfig {
        applicationId = "app.ravenreach.phone"
        minSdk = 31
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    // Google's on-device QR scanner UI (no camera permission needed by this app).
    implementation("com.google.android.gms:play-services-code-scanner:16.1.0")
    // Draws this phone's pairing QR code (pure Java, no camera).
    implementation("com.google.zxing:core:3.5.3")
    testImplementation("junit:junit:4.13.2")
}
