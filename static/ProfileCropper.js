(function () {
    'use strict';

    const OUTPUT_SIZE = 512;
    const MAX_ZOOM = 4;
    let active = null;

    function buildDialog() {
        const overlay = document.createElement('div');
        overlay.className = 'pf-cropper-overlay';
        overlay.hidden = true;
        overlay.innerHTML = `
            <div class="pf-cropper-dialog" role="dialog" aria-modal="true" aria-labelledby="pf-cropper-title" aria-describedby="pf-cropper-help">
                <div class="pf-cropper-head">
                    <h2 id="pf-cropper-title">Adjust profile picture</h2>
                    <button type="button" class="pf-cropper-close" data-cropper-cancel aria-label="Cancel">&times;</button>
                </div>
                <p id="pf-cropper-help" class="pf-cropper-help">Drag to reposition. Use the slider, mouse wheel, or pinch to zoom.</p>
                <div class="pf-cropper-stage" tabindex="0" aria-label="Crop area. Use arrow keys to move and plus or minus to zoom.">
                    <canvas class="pf-cropper-canvas" width="${OUTPUT_SIZE}" height="${OUTPUT_SIZE}"></canvas>
                    <div class="pf-cropper-mask" aria-hidden="true"></div>
                </div>
                <div class="pf-cropper-controls">
                    <button type="button" class="pf-cropper-icon" data-cropper-zoom="-1" aria-label="Zoom out">&minus;</button>
                    <input type="range" class="pf-cropper-zoom" min="1" max="${MAX_ZOOM}" step="0.01" value="1" aria-label="Zoom">
                    <button type="button" class="pf-cropper-icon" data-cropper-zoom="1" aria-label="Zoom in">+</button>
                </div>
                <div class="pf-cropper-tools">
                    <button type="button" class="pf-cropper-secondary" data-cropper-rotate>&#8635; Rotate</button>
                    <button type="button" class="pf-cropper-secondary" data-cropper-reset>Reset</button>
                </div>
                <div class="pf-cropper-actions">
                    <button type="button" class="pf-cropper-secondary" data-cropper-cancel>Cancel</button>
                    <button type="button" class="pf-cropper-primary" data-cropper-save>Save picture</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        return overlay;
    }

    function loadImage(file) {
        return new Promise((resolve, reject) => {
            if (!file || !/^image\//i.test(file.type || '')) {
                reject(new Error('Choose a JPG, PNG, WEBP, or GIF image.'));
                return;
            }
            const url = URL.createObjectURL(file);
            const image = new Image();
            image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
            image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('This image could not be opened. Try a JPG or PNG file.')); };
            image.src = url;
        });
    }

    function open(file) {
        if (active) active.finish(null);
        return loadImage(file).then(image => new Promise(resolve => {
            const overlay = document.querySelector('.pf-cropper-overlay') || buildDialog();
            const stage = overlay.querySelector('.pf-cropper-stage');
            const canvas = overlay.querySelector('.pf-cropper-canvas');
            const zoomInput = overlay.querySelector('.pf-cropper-zoom');
            const saveButton = overlay.querySelector('[data-cropper-save]');
            const ctx = canvas.getContext('2d');
            const previousFocus = document.activeElement;
            const pointers = new Map();
            const state = { zoom: 1, rotation: 0, x: 0, y: 0 };
            let pinchStart = null;

            const rotatedSize = () => (state.rotation % 180 === 0
                ? { w: image.naturalWidth, h: image.naturalHeight }
                : { w: image.naturalHeight, h: image.naturalWidth });
            const baseScale = () => {
                const size = rotatedSize();
                return Math.max(OUTPUT_SIZE / size.w, OUTPUT_SIZE / size.h);
            };

            function clamp() {
                const size = rotatedSize();
                const scale = baseScale() * state.zoom;
                const maxX = Math.max(0, (size.w * scale - OUTPUT_SIZE) / 2);
                const maxY = Math.max(0, (size.h * scale - OUTPUT_SIZE) / 2);
                state.x = Math.min(maxX, Math.max(-maxX, state.x));
                state.y = Math.min(maxY, Math.max(-maxY, state.y));
            }

            function draw() {
                clamp();
                const scale = baseScale() * state.zoom;
                ctx.setTransform(1, 0, 0, 1, 0, 0);
                ctx.clearRect(0, 0, OUTPUT_SIZE, OUTPUT_SIZE);
                ctx.imageSmoothingQuality = 'high';
                ctx.translate(OUTPUT_SIZE / 2 + state.x, OUTPUT_SIZE / 2 + state.y);
                ctx.rotate(state.rotation * Math.PI / 180);
                ctx.scale(scale, scale);
                ctx.drawImage(image, -image.naturalWidth / 2, -image.naturalHeight / 2);
                zoomInput.value = String(state.zoom);
            }

            function setZoom(value) {
                const next = Math.min(MAX_ZOOM, Math.max(1, Number(value) || 1));
                const ratio = next / state.zoom;
                state.x *= ratio;
                state.y *= ratio;
                state.zoom = next;
                draw();
            }

            const canvasUnits = () => OUTPUT_SIZE / (stage.clientWidth || OUTPUT_SIZE);

            function onPointerDown(event) {
                stage.setPointerCapture?.(event.pointerId);
                pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
                if (pointers.size === 2) {
                    const [a, b] = [...pointers.values()];
                    pinchStart = { distance: Math.hypot(a.x - b.x, a.y - b.y) || 1, zoom: state.zoom };
                }
                stage.classList.add('is-dragging');
            }

            function onPointerMove(event) {
                const last = pointers.get(event.pointerId);
                if (!last) return;
                event.preventDefault();
                const current = { x: event.clientX, y: event.clientY };
                pointers.set(event.pointerId, current);
                if (pointers.size >= 2 && pinchStart) {
                    const [a, b] = [...pointers.values()];
                    setZoom(pinchStart.zoom * (Math.hypot(a.x - b.x, a.y - b.y) / pinchStart.distance));
                    return;
                }
                const units = canvasUnits();
                state.x += (current.x - last.x) * units;
                state.y += (current.y - last.y) * units;
                draw();
            }

            function onPointerUp(event) {
                pointers.delete(event.pointerId);
                if (pointers.size < 2) pinchStart = null;
                if (!pointers.size) stage.classList.remove('is-dragging');
            }

            function onWheel(event) {
                event.preventDefault();
                setZoom(state.zoom * (event.deltaY < 0 ? 1.08 : 1 / 1.08));
            }

            function onStageKey(event) {
                const step = event.shiftKey ? 40 : 12;
                const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
                if (moves[event.key]) {
                    event.preventDefault();
                    state.x += moves[event.key][0];
                    state.y += moves[event.key][1];
                    draw();
                } else if (event.key === '+' || event.key === '=') {
                    event.preventDefault();
                    setZoom(state.zoom + 0.1);
                } else if (event.key === '-' || event.key === '_') {
                    event.preventDefault();
                    setZoom(state.zoom - 0.1);
                }
            }

            function onKeyDown(event) {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopPropagation();
                    finish(null);
                    return;
                }
                if (event.key !== 'Tab') return;
                const focusable = [...overlay.querySelectorAll('button, input, [tabindex="0"]')].filter(element => !element.disabled);
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            }

            function onClick(event) {
                const target = event.target.closest('button');
                if (!target) return;
                if (target.hasAttribute('data-cropper-cancel')) finish(null);
                else if (target.hasAttribute('data-cropper-reset')) { Object.assign(state, { zoom: 1, rotation: 0, x: 0, y: 0 }); draw(); }
                else if (target.hasAttribute('data-cropper-rotate')) { state.rotation = (state.rotation + 90) % 360; state.x = 0; state.y = 0; draw(); }
                else if (target.hasAttribute('data-cropper-zoom')) setZoom(state.zoom + Number(target.dataset.cropperZoom) * 0.25);
                else if (target.hasAttribute('data-cropper-save')) {
                    saveButton.disabled = true;
                    saveButton.textContent = 'Saving...';
                    canvas.toBlob(blob => finish(blob), 'image/png');
                }
            }

            const onZoomInput = () => setZoom(zoomInput.value);

            function finish(blob) {
                if (active !== controller) return;
                active = null;
                overlay.hidden = true;
                document.documentElement.classList.remove('pf-cropper-open');
                overlay.removeEventListener('click', onClick);
                overlay.removeEventListener('keydown', onKeyDown, true);
                stage.removeEventListener('pointerdown', onPointerDown);
                stage.removeEventListener('pointermove', onPointerMove);
                stage.removeEventListener('pointerup', onPointerUp);
                stage.removeEventListener('pointercancel', onPointerUp);
                stage.removeEventListener('wheel', onWheel);
                stage.removeEventListener('keydown', onStageKey);
                zoomInput.removeEventListener('input', onZoomInput);
                saveButton.disabled = false;
                saveButton.textContent = 'Save picture';
                if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
                resolve(blob);
            }

            const controller = { finish };
            active = controller;
            overlay.addEventListener('click', onClick);
            overlay.addEventListener('keydown', onKeyDown, true);
            stage.addEventListener('pointerdown', onPointerDown);
            stage.addEventListener('pointermove', onPointerMove);
            stage.addEventListener('pointerup', onPointerUp);
            stage.addEventListener('pointercancel', onPointerUp);
            stage.addEventListener('wheel', onWheel, { passive: false });
            stage.addEventListener('keydown', onStageKey);
            zoomInput.addEventListener('input', onZoomInput);

            draw();
            overlay.hidden = false;
            document.documentElement.classList.add('pf-cropper-open');
            stage.focus();
        }));
    }

    window.PathFinderCropper = { open, OUTPUT_SIZE };
})();
