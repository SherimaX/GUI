// Manage hold-to-activate behaviour for toggle buttons
function setupHoldButton(id){
    var btn = document.getElementById(id);
    if(!btn) return;
    function start(){
        btn.dataset.holdStart = Date.now();
        btn.dataset.holdDuration = '0';
        btn.classList.add('holding');
    }
    function end(){
        if(!btn.dataset.holdStart) return;
        var dur = Date.now() - parseInt(btn.dataset.holdStart, 10);
        btn.dataset.holdDuration = String(dur);
        btn.classList.remove('holding');
        delete btn.dataset.holdStart;
    }
    function cancel(){
        btn.dataset.holdDuration = '0';
        btn.classList.remove('holding');
        delete btn.dataset.holdStart;
    }
    btn.addEventListener('mousedown', start);
    btn.addEventListener('touchstart', start);
    btn.addEventListener('mouseup', end);
    btn.addEventListener('touchend', end);
    btn.addEventListener('mouseleave', cancel);
    btn.addEventListener('touchcancel', cancel);
}

document.addEventListener('DOMContentLoaded', function(){
    ['motor-btn','assist-btn','k-btn'].forEach(setupHoldButton);
});
