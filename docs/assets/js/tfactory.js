/* TFactory Pages — animations + interactive bits.
 * Pure vanilla, no dependencies. Loaded with `defer`.
 *
 * 1. IntersectionObserver reveals: any element with class="reveal"
 *    gets ".reveal--visible" added when it enters the viewport.
 * 2. Stat counters: any [data-counter="N"] animates from 0 → N
 *    on first intersect (~1.4s ease-out).
 * 3. Pipeline node ripple: clicking a pipeline node fires a brief
 *    cyan ripple effect; touchscreen-friendly.
 *
 * Honours prefers-reduced-motion — skips all animation and just
 * applies the final state on connect.
 */

(function () {
  'use strict';

  var reducedMotion =
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ─── Reveal on scroll ─────────────────────────────────────────── */

  function setupReveals() {
    var els = document.querySelectorAll('.reveal');
    if (!els.length) return;

    if (reducedMotion || !('IntersectionObserver' in window)) {
      els.forEach(function (el) {
        el.classList.add('reveal--visible');
      });
      return;
    }

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add('reveal--visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: '0px 0px -50px 0px' }
    );

    els.forEach(function (el) {
      observer.observe(el);
    });
  }

  /* ─── Stat counters ────────────────────────────────────────────── */

  function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
  }

  function animateCount(el, target, duration) {
    if (reducedMotion) {
      el.textContent = formatNumber(target);
      return;
    }
    var start = performance.now();
    function tick(now) {
      var t = Math.min(1, (now - start) / duration);
      var value = Math.round(easeOutCubic(t) * target);
      el.textContent = formatNumber(value);
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function formatNumber(n) {
    // Add thousands separators for readability
    return n.toLocaleString('en-US');
  }

  function setupCounters() {
    var els = document.querySelectorAll('[data-counter]');
    if (!els.length) return;

    if (!('IntersectionObserver' in window)) {
      els.forEach(function (el) {
        animateCount(el, parseInt(el.dataset.counter, 10) || 0, 1400);
      });
      return;
    }

    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            var target = parseInt(entry.target.dataset.counter, 10) || 0;
            animateCount(entry.target, target, 1400);
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.5 }
    );

    els.forEach(function (el) {
      el.textContent = '0';
      observer.observe(el);
    });
  }

  /* ─── Pipeline node ripple ────────────────────────────────────── */

  function setupPipelineRipple() {
    if (reducedMotion) return;
    var nodes = document.querySelectorAll('.pipeline__node');
    nodes.forEach(function (node) {
      node.addEventListener('click', function (e) {
        var rect = node.getBoundingClientRect();
        var ripple = document.createElement('span');
        ripple.className = 'ripple';
        ripple.style.left = e.clientX - rect.left + 'px';
        ripple.style.top = e.clientY - rect.top + 'px';
        node.appendChild(ripple);
        setTimeout(function () {
          ripple.remove();
        }, 700);
      });
    });
  }

  /* ─── Boot ─────────────────────────────────────────────────────── */

  function boot() {
    setupReveals();
    setupCounters();
    setupPipelineRipple();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
