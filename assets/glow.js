// Apply colored glow to all Plotly scatter lines
function applyGlow() {
    document.querySelectorAll('.js-plotly-plot .js-line').forEach(function(el) {
        const stroke = el.style.stroke || el.getAttribute('stroke');
        if (!stroke) return;
        let glow = stroke;
        if (stroke.startsWith('rgb')) {
            const nums = stroke.match(/\d+/g);
            if (nums && nums.length >= 3) {
                glow = `rgba(${nums[0]}, ${nums[1]}, ${nums[2]}, 0.35)`;
            }
        } else if (stroke.startsWith('#') && stroke.length === 7) {
            const r = parseInt(stroke.slice(1,3), 16);
            const g = parseInt(stroke.slice(3,5), 16);
            const b = parseInt(stroke.slice(5,7), 16);
            glow = `rgba(${r}, ${g}, ${b}, 0.35)`;
        }
        el.style.filter = `drop-shadow(0 0 6px ${glow})`;
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyGlow);
} else {
    applyGlow();
}

