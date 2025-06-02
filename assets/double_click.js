// Detect double-click within 500ms and expose flag via dataset
function setupDoubleClickButton(id){
    var btn = document.getElementById(id);
    if(!btn) return;
    btn.dataset.lastClick = '0';
    btn.dataset.doubleClick = '0';
    btn.addEventListener('click', function(){
        var now = Date.now();
        var last = parseInt(btn.dataset.lastClick || '0', 10);
        if(now - last <= 500){
            btn.dataset.doubleClick = '1';
        } else {
            btn.dataset.doubleClick = '0';
        }
        btn.dataset.lastClick = String(now);
    });
}

function initDoubleClickButtons(){
    ['zero-btn','motor-btn','assist-btn','k-btn'].forEach(setupDoubleClickButton);
}

if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', initDoubleClickButtons);
} else {
    initDoubleClickButtons();
}
