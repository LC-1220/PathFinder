(() => {
    const dialog = document.getElementById('logoutPopup');
    if(!dialog) return;
    document.querySelectorAll('[data-student-logout]').forEach(link => {
        link.addEventListener('click', event => {
            event.preventDefault();
            if(!dialog.open) dialog.showModal();
        });
    });
    dialog.addEventListener('click', event => {
        if(event.target === dialog) dialog.close();
    });
    dialog.addEventListener('keydown', event => {
        if(event.key === 'Escape'){
            event.preventDefault();
            dialog.close();
        }
    });
})();