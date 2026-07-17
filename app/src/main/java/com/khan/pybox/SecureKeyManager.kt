package com.khan.pybox

import android.content.Context
import androidx.security.crypto.EncryptedFile
import androidx.security.crypto.MasterKey
import java.io.File
import java.security.SecureRandom

/**
 * Generates and stores a 256-bit AES key, wrapped by the Android Keystore
 * (via Jetpack Security's EncryptedFile/MasterKey), so the raw key material
 * is never sitting on disk in plaintext. The key is decrypted in-memory
 * each app start and handed to the Python backend so encryption.py can use
 * it for AES-GCM on local DB backups / exports.
 *
 * This encrypts data AT REST on this device - it does not send anything
 * anywhere, and does not protect against someone with root/adb access to
 * an unlocked, already-decrypted device (nothing running purely in app
 * storage can). What it does protect: a phone backup, a copied file, or
 * someone browsing the SD card without the Keystore's device-bound key.
 */
object SecureKeyManager {

    private const val KEY_FILE_NAME = "pybox_master.key.enc"
    private const val KEY_BYTES = 32 // AES-256

    fun getOrCreateKeyHex(context: Context): String {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()

        val keyFile = File(context.filesDir, KEY_FILE_NAME)

        if (!keyFile.exists()) {
            val raw = ByteArray(KEY_BYTES)
            SecureRandom().nextBytes(raw)
            val encryptedFile = EncryptedFile.Builder(
                context, keyFile, masterKey, EncryptedFile.FileEncryptionScheme.AES256_GCM_HKDF_4KB
            ).build()
            encryptedFile.openFileOutput().use { it.write(raw) }
            return raw.joinToString("") { "%02x".format(it) }
        }

        val encryptedFile = EncryptedFile.Builder(
            context, keyFile, masterKey, EncryptedFile.FileEncryptionScheme.AES256_GCM_HKDF_4KB
        ).build()
        val bytes = encryptedFile.openFileInput().use { it.readBytes() }
        return bytes.joinToString("") { "%02x".format(it) }
    }

    /** Wipes the key - used by "Reset app data" so old encrypted backups
     * become permanently unreadable rather than silently orphaned. */
    fun wipeKey(context: Context) {
        File(context.filesDir, KEY_FILE_NAME).delete()
    }
}
