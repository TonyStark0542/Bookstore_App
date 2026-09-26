/* Shared storefront behaviour: cart badge, add-to-cart, toast, search filter. */

function showToast(message) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => { toast.hidden = true; }, 2400);
}

function updateCartBadge() {
    const badge = document.getElementById('cart-badge');
    if (!badge || document.body.dataset.auth !== '1') return;
    fetch('/api/cart')
        .then(response => (response.ok ? response.json() : null))
        .then(cart => {
            if (!cart) return;
            badge.textContent = cart.count;
            badge.hidden = cart.count === 0;
        })
        .catch(() => {});
}

function addToCart(bookId, qty) {
    if (document.body.dataset.auth !== '1') {
        window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
        return;
    }
    fetch('/api/cart', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({book_id: bookId, qty: qty || 1}),
    })
        .then(response => response.json().then(data => {
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
            return data;
        }))
        .then(cart => {
            const badge = document.getElementById('cart-badge');
            if (badge) {
                badge.textContent = cart.count;
                badge.hidden = cart.count === 0;
            }
            showToast('Added to cart');
        })
        .catch(err => showToast('Could not add to cart: ' + err.message));
}

document.addEventListener('DOMContentLoaded', () => {
    updateCartBadge();

    // Search filters as you type; the button/Enter shouldn't reload the page
    const searchForm = document.getElementById('search-form');
    if (searchForm) searchForm.addEventListener('submit', event => event.preventDefault());

    // One delegated listener covers every "Add to cart" button on the page
    document.addEventListener('click', event => {
        const btn = event.target.closest('.add-btn');
        if (!btn || btn.disabled) return;
        event.preventDefault();
        let qty = 1;
        if (btn.dataset.qtySource) {
            qty = parseInt(document.getElementById(btn.dataset.qtySource).textContent, 10) || 1;
        }
        addToCart(btn.dataset.bookId, qty);
    });

    // Header search filters the visible book grid by title/author
    const searchInput = document.getElementById('search-input');
    const grid = document.getElementById('books-grid');
    if (searchInput && grid) {
        searchInput.addEventListener('input', () => {
            const query = searchInput.value.trim().toLowerCase();
            let visible = 0;
            grid.querySelectorAll('[data-book-card]').forEach(card => {
                const match = !query || card.dataset.search.includes(query);
                card.style.display = match ? '' : 'none';
                if (match) visible++;
            });
            const noResults = document.getElementById('no-results');
            if (noResults) noResults.hidden = visible > 0;
        });
    }
});
