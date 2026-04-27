/**
 * FCM Token Registration - Android Bridge
 * Автоматически регистрирует FCM токен на всех страницах
 * Подключить в <head> всех HTML страниц: <script src="/libs/fcm-bridge.js"></script>
 */

(function() {
    console.log('🔔 FCM Bridge script loaded');
    
    // Проверяем что мы в Android приложении
    if (typeof AndroidBridge === 'undefined') {
        console.log('ℹ️ Not in Android app - skipping FCM registration');
        return;
    }
    
    console.log('✅ AndroidBridge detected!');
    
    // Получаем токен из localStorage
    const getAuthToken = () => {
        return localStorage.getItem('dash_token') || localStorage.getItem('instr_token');
    };
    
    // Функция регистрации FCM токена
    const registerFCM = () => {
        const authToken = getAuthToken();
        
        if (!authToken) {
            console.log('⚠️ No auth token found - user not logged in yet');
            return false;
        }
        
        console.log('✅ Auth token found, attempting FCM registration...');
        
        if (!AndroidBridge.registerFCMToken) {
            console.error('❌ AndroidBridge.registerFCMToken method not found!');
            return false;
        }
        
        try {
            console.log('📞 Calling AndroidBridge.registerFCMToken');
            AndroidBridge.registerFCMToken(authToken);
            console.log('✅ FCM registration call completed');
            return true;
        } catch (e) {
            console.error('❌ Error calling AndroidBridge:', e);
            return false;
        }
    };
    
    // Callback от MainActivity когда FCM токен готов
    window.onFCMTokenReady = function(token) {
        console.log('✅ FCM Token ready from native callback!');
        registerFCM();
    };
    
    // Делаем ОДНУ попытку при загрузке (если токен уже готов)
    setTimeout(() => {
        console.log('🔄 Checking if FCM token already available...');
        const success = registerFCM();
        if (success) {
            console.log('✅ FCM registration successful on first attempt');
        } else {
            console.log('⏳ Waiting for FCM token from native callback...');
        }
    }, 1000); // Ждем 1 секунду после загрузки страницы
    
    console.log('✅ FCM Bridge initialized');
})();
