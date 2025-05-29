(function(){
    function updateFromScroll(){
        var cont = document.querySelector('.swipe-container');
        var highlight = document.getElementById('tabHighlight');
        var angleBtn = document.getElementById('tab-angle');
        var insoleBtn = document.getElementById('tab-insole');
        if(!cont || !highlight || !angleBtn || !insoleBtn){ return; }
        var idx = Math.round(cont.scrollLeft / cont.clientWidth);
        if(idx < 0){ idx = 0; }
        if(idx > 1){ idx = 1; }
        highlight.style.transform = 'translateX(' + (idx*100) + '%)';
        angleBtn.classList.toggle('active', idx === 0);
        insoleBtn.classList.toggle('active', idx === 1);
    }
    document.addEventListener('DOMContentLoaded', function(){
        var cont = document.querySelector('.swipe-container');
        if(!cont) return;
        cont.addEventListener('scroll', function(){
            window.requestAnimationFrame(updateFromScroll);
        });
    });
})();
