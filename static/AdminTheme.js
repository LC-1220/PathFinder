try { document.documentElement.classList.toggle('admin-dark', localStorage.getItem('pathfinder-admin-theme') === 'dark'); } catch (error) {}
