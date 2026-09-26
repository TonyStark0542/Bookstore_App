/* Admin panel: users/roles, orders (advance/revert/cancel), catalog CRUD +
   bulk stock import, and the activity log. */

function adminToast(message) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(adminToast._timer);
    adminToast._timer = setTimeout(() => { toast.hidden = true; }, 2400);
}

function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
}

/* ---------- Orders: advance / revert / cancel ---------- */

function renderOrder(order) {
    const nextStage = order.stages[order.stage_index + 1];
    const timeline = order.stages.map((stage, i) => `
        <li class="${i < order.stage_index ? 'done' : ''}${i === order.stage_index ? ' current' : ''}">
            <span class="dot"></span><span class="stage-name">${escapeHtml(stage)}</span>
        </li>`).join('');
    const items = order.items.map(item => `
        <li>
            <a href="/books/${item.book_id}">${escapeHtml(item.title)}</a>
            <span class="by">${escapeHtml(item.author)} · ×${item.qty}</span>
            <span class="price">$${item.line_total.toFixed(2)}</span>
        </li>`).join('');

    let actions;
    if (order.cancelled) {
        actions = `<span class="pill out">Cancelled</span>`;
    } else {
        const parts = [];
        if (order.stage_index > 0) {
            parts.push(`<button type="button" class="btn ghost revert-btn" data-order-id="${order.order_id}">Move back</button>`);
        }
        parts.push(nextStage
            ? `<button type="button" class="btn advance-btn" data-order-id="${order.order_id}">Move to ${escapeHtml(nextStage)}</button>`
            : `<span class="pill in">Delivered</span>`);
        parts.push(`<button type="button" class="btn ghost cancel-btn" data-order-id="${order.order_id}">Cancel order</button>`);
        actions = parts.join(' ');
    }

    return `
    <article class="order-card" data-order-id="${order.order_id}">
        <header class="order-head">
            <div>
                <h3>Order #${order.short_id}</h3>
                <p class="order-meta">${order.placed_at} · ${escapeHtml(order.payment.detail || order.payment.method.toUpperCase())}
                    · ${order.payment.txn_id}</p>
            </div>
            <div class="order-head-right">
                <span class="pill ${order.payment.status === 'PAID' ? 'in' : 'low'}">
                    ${order.payment.status === 'PAID' ? 'Paid' : 'Pay on delivery'}
                </span>
                <span class="price">$${order.total.toFixed(2)}</span>
            </div>
        </header>
        <ol class="timeline">${timeline}</ol>
        <ul class="order-items">${items}</ul>
        <div class="admin-action-row">${actions}</div>
    </article>`;
}

function loadOrders(card) {
    const panel = card.querySelector('.admin-user-orders');
    panel.innerHTML = '<p class="empty-state">Loading orders…</p>';
    fetch(`/api/admin/orders/${card.dataset.userId}`)
        .then(response => response.json())
        .then(orders => {
            panel.dataset.loaded = '1';
            panel.innerHTML = orders.length
                ? orders.map(renderOrder).join('')
                : '<p class="empty-state">No orders yet.</p>';
            panel.querySelectorAll('.advance-btn').forEach(btn => btn.addEventListener('click', onAdvance));
            panel.querySelectorAll('.revert-btn').forEach(btn => btn.addEventListener('click', onRevert));
            panel.querySelectorAll('.cancel-btn').forEach(btn => btn.addEventListener('click', onCancel));
        })
        .catch(() => { panel.innerHTML = '<p class="empty-state">Could not load orders.</p>'; });
}

function orderAction(btn, url, busyText, onSuccess) {
    const card = btn.closest('.admin-user-card');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = busyText;
    fetch(url, {method: 'POST'})
        .then(response => response.json().then(data => {
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
            return data;
        }))
        .then(data => { onSuccess(data); loadOrders(card); })
        .catch(err => {
            btn.disabled = false;
            btn.textContent = originalText;
            adminToast(err.message);
        });
}

function onAdvance(event) {
    const btn = event.currentTarget;
    const orderId = btn.dataset.orderId;
    orderAction(btn, `/api/admin/orders/${orderId}/advance`, 'Updating…',
        data => adminToast(`Order #${orderId.slice(-8).toUpperCase()} moved to ${data.status}`));
}

function onRevert(event) {
    const btn = event.currentTarget;
    const orderId = btn.dataset.orderId;
    orderAction(btn, `/api/admin/orders/${orderId}/revert`, 'Updating…',
        data => adminToast(`Order #${orderId.slice(-8).toUpperCase()} moved back to ${data.status}`));
}

function onCancel(event) {
    const btn = event.currentTarget;
    const orderId = btn.dataset.orderId;
    if (!confirm('Cancel this order?')) return;
    orderAction(btn, `/api/admin/orders/${orderId}/cancel`, 'Cancelling…',
        () => adminToast(`Order #${orderId.slice(-8).toUpperCase()} cancelled`));
}

/* ---------- Users: role + disable/enable ---------- */

function onToggleRole(event) {
    const btn = event.currentTarget;
    const card = btn.closest('.admin-user-card');
    const userId = card.dataset.userId;
    const makeAdmin = card.dataset.isAdmin !== 'true';
    btn.disabled = true;
    fetch(`/api/admin/users/${userId}/role`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({is_admin: makeAdmin}),
    })
        .then(response => response.json().then(data => {
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
            return data;
        }))
        .then(() => {
            adminToast(makeAdmin ? 'Admin access granted' : 'Admin access revoked');
            location.reload();
        })
        .catch(err => { btn.disabled = false; adminToast(err.message); });
}

function onToggleStatus(event) {
    const btn = event.currentTarget;
    const card = btn.closest('.admin-user-card');
    const userId = card.dataset.userId;
    const disable = card.dataset.disabled !== 'true';
    btn.disabled = true;
    fetch(`/api/admin/users/${userId}/status`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({disabled: disable}),
    })
        .then(response => response.json().then(data => {
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
            return data;
        }))
        .then(() => {
            adminToast(disable ? 'Account disabled' : 'Account re-enabled');
            location.reload();
        })
        .catch(err => { btn.disabled = false; adminToast(err.message); });
}

function applyUserFilter() {
    const query = (document.getElementById('user-search')?.value || '').toLowerCase();
    document.querySelectorAll('#admin-users-list .admin-user-card').forEach(card => {
        card.hidden = query.length > 0 && !card.textContent.toLowerCase().includes(query);
    });
}

/* ---------- Catalog & stock: list, add, edit, delete, bulk CSV ---------- */

let loadedBooks = [];

function renderBookRow(book) {
    const lowStock = book.stock < 5;
    return `
    <article class="admin-user-card" data-book-id="${book._id}">
        <div class="admin-book-fields">
            <input type="text" class="book-title-input" value="${escapeHtml(book.title)}" placeholder="Title">
            <input type="text" class="book-author-input" value="${escapeHtml(book.author)}" placeholder="Author">
            <input type="text" class="book-category-input" value="${escapeHtml(book.category || '')}" placeholder="Category">
            <input type="number" min="0" step="0.01" class="book-price-input" value="${book.price}" placeholder="Price">
            <input type="number" min="0" class="stock-input" value="${book.stock}" placeholder="Stock">
            ${lowStock ? '<span class="pill low">Low stock</span>' : ''}
        </div>
        <div class="admin-user-actions">
            <button type="button" class="btn book-save-btn">Save</button>
            <button type="button" class="btn ghost book-delete-btn">Delete</button>
        </div>
    </article>`;
}

function onSaveBook(event) {
    const btn = event.currentTarget;
    const card = btn.closest('.admin-user-card');
    const bookId = card.dataset.bookId;
    const title = card.querySelector('.book-title-input').value.trim();
    const author = card.querySelector('.book-author-input').value.trim();
    const category = card.querySelector('.book-category-input').value.trim();
    const price = parseFloat(card.querySelector('.book-price-input').value);
    const stock = parseInt(card.querySelector('.stock-input').value, 10);

    if (!title || !author || !category || isNaN(price) || price < 0 || isNaN(stock) || stock < 0) {
        adminToast('Check the book fields — something is invalid');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Saving…';
    Promise.all([
        fetch(`/api/admin/books/${bookId}`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({title, author, category, price}),
        }).then(r => r.json().then(d => ({ok: r.ok, data: d}))),
        fetch(`/api/admin/books/${bookId}/stock`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({stock}),
        }).then(r => r.json().then(d => ({ok: r.ok, data: d}))),
    ])
        .then(([bookResult, stockResult]) => {
            if (!bookResult.ok) throw new Error(bookResult.data.error || 'Could not save book details');
            if (!stockResult.ok) throw new Error(stockResult.data.error || 'Could not save stock');
            adminToast(`${title}: saved`);
            loadBooks();
        })
        .catch(err => {
            btn.disabled = false;
            btn.textContent = 'Save';
            adminToast(err.message);
        });
}

function onDeleteBook(event) {
    const btn = event.currentTarget;
    const card = btn.closest('.admin-user-card');
    const bookId = card.dataset.bookId;
    const title = card.querySelector('.book-title-input').value;
    if (!confirm(`Delete "${title}"? This cannot be undone.`)) return;
    btn.disabled = true;
    fetch(`/api/admin/books/${bookId}`, {method: 'DELETE'})
        .then(response => response.json().then(data => {
            if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
            return data;
        }))
        .then(() => { adminToast(`${title}: deleted`); loadBooks(); })
        .catch(err => { btn.disabled = false; adminToast(err.message); });
}

function loadBooks() {
    const container = document.getElementById('admin-books');
    if (!container) return;
    fetch('/api/admin/books')
        .then(response => response.json())
        .then(books => {
            loadedBooks = books;
            container.innerHTML = books.length
                ? books.map(renderBookRow).join('')
                : '<p class="empty-state">No books yet.</p>';
            container.querySelectorAll('.book-save-btn').forEach(btn => btn.addEventListener('click', onSaveBook));
            container.querySelectorAll('.book-delete-btn').forEach(btn => btn.addEventListener('click', onDeleteBook));
            applyBookFilter();
        })
        .catch(() => { container.innerHTML = '<p class="empty-state">Could not load catalog.</p>'; });
}

function applyBookFilter() {
    const query = (document.getElementById('book-search')?.value || '').toLowerCase();
    document.querySelectorAll('#admin-books .admin-user-card').forEach(card => {
        card.hidden = query.length > 0 && !card.textContent.toLowerCase().includes(query);
    });
}

function onAddBook(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    data.price = parseFloat(data.price);
    data.stock = parseInt(data.stock, 10);
    fetch('/api/admin/books', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data),
    })
        .then(response => response.json().then(payload => {
            if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
            return payload;
        }))
        .then(payload => {
            adminToast(`Added "${payload.title}"`);
            form.reset();
            loadBooks();
        })
        .catch(err => adminToast(err.message));
}

function onBulkStockFile(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
        const updates = [];
        reader.result.split(/\r?\n/).map(l => l.trim()).filter(Boolean).forEach(line => {
            const [titleRaw, stockRaw] = line.split(',');
            if (!titleRaw || stockRaw === undefined) return;
            const title = titleRaw.trim();
            const stock = parseInt(stockRaw.trim(), 10);
            const match = loadedBooks.find(b => b.title.toLowerCase() === title.toLowerCase());
            if (match && !isNaN(stock) && stock >= 0) updates.push({id: match._id, title: match.title, stock});
        });
        if (!updates.length) {
            adminToast('No matching books found in the CSV');
            event.target.value = '';
            return;
        }
        Promise.all(updates.map(u => fetch(`/api/admin/books/${u.id}/stock`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({stock: u.stock}),
        })))
            .then(() => { adminToast(`Bulk updated stock for ${updates.length} book(s)`); loadBooks(); })
            .catch(() => adminToast('Some bulk updates failed'))
            .finally(() => { event.target.value = ''; });
    };
    reader.readAsText(file);
}

/* ---------- Activity log ---------- */

function renderAuditEntry(entry) {
    return `<li>
        <span class="audit-actor">${escapeHtml(entry.actor)}</span>
        <span class="audit-action">${escapeHtml(entry.action)}</span> — ${escapeHtml(entry.detail)}
        <span class="order-meta">${escapeHtml(entry.at)}</span>
    </li>`;
}

function loadAuditLog() {
    const container = document.getElementById('admin-audit-log');
    if (!container) return;
    fetch('/api/admin/audit-log')
        .then(response => response.json())
        .then(entries => {
            container.innerHTML = entries.length
                ? `<ul class="audit-list">${entries.map(renderAuditEntry).join('')}</ul>`
                : '<p class="empty-state">No admin activity yet.</p>';
        })
        .catch(() => { container.innerHTML = '<p class="empty-state">Could not load activity log.</p>'; });
}

/* ---------- Wire everything up ---------- */

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-toggle]').forEach(toggleBtn => {
        toggleBtn.addEventListener('click', () => {
            const card = toggleBtn.closest('.admin-user-card');
            const panel = card.querySelector('.admin-user-orders');
            const opening = panel.hidden;
            panel.hidden = !opening;
            card.toggleAttribute('data-open', opening);
            if (opening && !panel.dataset.loaded) loadOrders(card);
        });
    });
    document.querySelectorAll('.role-btn').forEach(btn => btn.addEventListener('click', onToggleRole));
    document.querySelectorAll('.status-btn').forEach(btn => btn.addEventListener('click', onToggleStatus));

    document.getElementById('user-search')?.addEventListener('input', applyUserFilter);
    document.getElementById('book-search')?.addEventListener('input', applyBookFilter);
    document.getElementById('add-book-form')?.addEventListener('submit', onAddBook);
    document.getElementById('bulk-stock-file')?.addEventListener('change', onBulkStockFile);

    loadBooks();
    loadAuditLog();
});
