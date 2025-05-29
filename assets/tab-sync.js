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

        var tabWidth = cont.clientWidth / 2;
        var offset = idx * tabWidth;
        highlight.style.width = tabWidth + 'px';
        highlight.style.transform = 'translateX(' + offset + 'px)';

        angleBtn.classList.toggle('active', idx === 0);
        insoleBtn.classList.toggle('active', idx === 1);
    }

    function init(){
        var cont = document.querySelector('.swipe-container');
        if(!cont) return;
        cont.addEventListener('scroll', function(){
            window.requestAnimationFrame(updateFromScroll);
        });
        updateFromScroll();
    }

    if(document.readyState === 'loading'){
        document.addEventListener('DOMContentLoaded', init);
    }else{
        init();
    }
})();
