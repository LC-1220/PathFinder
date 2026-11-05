(() => {
    const heartbeatInterval = 30_000;
    let expiresAt = 0;
    let lastHeartbeat = 0;
    let pending = false;
    let timer;
    let activityTimer;
    let redirected = false;

    function clearReviewedGrades(){
        Object.keys(sessionStorage).filter(key => key.startsWith('pathfinder-reviewed-grades:'))
            .forEach(key => sessionStorage.removeItem(key));
    }

    async function syncSession(activity = false){
        if(pending || redirected) return;
        pending = true;
        try{
            const response = await fetch('/api/v1/session', {
                method: activity ? 'POST' : 'GET',
                cache: 'no-store',
                headers: {Accept: 'application/json'}
            });
            const data = await response.json();
            if(response.status === 401){
                redirected = true;
                try{ clearReviewedGrades(); }catch(error){ console.error('Could not clear reviewed grades:', error); }
                location.replace(data.redirect || '/login?reason=timeout');
                return;
            }
            if(!response.ok || !data.success) throw new Error(data.message || 'Could not verify session expiry.');
            expiresAt = data.expires_at * 1000;
            if(activity) lastHeartbeat = Date.now();
            clearTimeout(timer);
            timer = setTimeout(() => syncSession(), Math.max(0, expiresAt - Date.now()));
        }catch(error){
            console.error('Session check failed:', error);
            if(expiresAt && Date.now() >= expiresAt){
                redirected = true;
                location.replace('/logout?reason=timeout');
            }
        }finally{
            pending = false;
        }
    }

    function activity(){
        const now = Date.now();
        if(!expiresAt || now >= expiresAt){
            syncSession();
        }else if(now - lastHeartbeat >= heartbeatInterval){
            clearTimeout(activityTimer);
            syncSession(true);
        }else{
            clearTimeout(activityTimer);
            activityTimer = setTimeout(() => syncSession(true), heartbeatInterval - (now - lastHeartbeat));
        }
    }

    ['pointerdown', 'pointermove', 'keydown', 'scroll', 'touchstart'].forEach(event => {
        document.addEventListener(event, activity, {passive: true, capture: true});
    });
    document.addEventListener('visibilitychange', () => {
        if(document.visibilityState === 'visible') syncSession();
    });
    setInterval(() => syncSession(), heartbeatInterval);
    syncSession();
})();
