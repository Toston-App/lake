// Configuration
const API_BASE = 'http://localhost:8888/api/v1';
let authToken = ""; // Set your auth token here

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    checkApiConnection();
    
    // Set default date for transaction form
    const txDateInput = document.getElementById('tx-date');
    if (txDateInput) {
        txDateInput.value = new Date().toISOString().slice(0, 16);
    }
});

// Navigation
function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const view = item.dataset.view;
            showView(view);
        });
    });
}

function showView(viewName) {
    // Update nav
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.view === viewName);
    });
    
    // Update views
    document.querySelectorAll('.view').forEach(view => {
        view.classList.toggle('active', view.id === `${viewName}-view`);
    });
    
    // Load data for the view
    switch (viewName) {
        case 'dashboard':
            loadDashboard();
            break;
        case 'assets':
            loadAssets();
            break;
        case 'holdings':
            loadHoldings();
            break;
        case 'transactions':
            loadTransactions();
            break;
    }
}

// API Functions
async function apiRequest(endpoint, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers,
    };
    
    if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
    }
    
    try {
        const response = await fetch(`${API_BASE}${endpoint}`, {
            ...options,
            headers,
        });
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
            throw new Error(error.detail || `HTTP ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
}

async function checkApiConnection() {
    const statusDot = document.querySelector('.status-dot');
    const statusText = document.getElementById('api-status-text');
    
    try {
        await fetch(`${API_BASE}/utils/health-check/`);
        statusDot.classList.add('connected');
        statusDot.classList.remove('error');
        statusText.textContent = 'API Connected';
        
        // Load initial data
        loadDashboard();
    } catch (error) {
        statusDot.classList.add('error');
        statusDot.classList.remove('connected');
        statusText.textContent = 'API Offline';
        showToast('Cannot connect to API. Make sure the backend is running.', 'error');
    }
}

// Dashboard
async function loadDashboard() {
    try {
        // Load portfolio summary
        const summary = await apiRequest('/investments/portfolio/summary');
        document.getElementById('total-value-usd').textContent = formatCurrency(summary.total_value_usd, 'USD');
        document.getElementById('total-value-mxn').textContent = formatCurrency(summary.total_value_mxn, 'MXN');
        document.getElementById('total-invested').textContent = formatCurrency(summary.total_invested, 'USD');
        document.getElementById('total-gain-loss').textContent = formatCurrency(summary.total_gain_loss, 'USD');
        
        const changeEl = document.getElementById('total-change');
        changeEl.textContent = `${summary.total_gain_loss_pct >= 0 ? '+' : ''}${summary.total_gain_loss_pct.toFixed(2)}%`;
        changeEl.className = `card-change ${summary.total_gain_loss_pct >= 0 ? 'positive' : 'negative'}`;
        
        // Load allocations
        loadAllocationByClass();
        loadAllocationByCurrency();
        loadAllocationByMarket();
        loadTopHoldings();
        
    } catch (error) {
        console.error('Failed to load dashboard:', error);
    }
}

async function loadAllocationByClass() {
    try {
        const data = await apiRequest('/investments/portfolio/allocation/by-class');
        renderAllocation('allocation-class', data.allocations, 'alloc-');
    } catch (error) {
        console.error('Failed to load allocation by class:', error);
    }
}

async function loadAllocationByCurrency() {
    try {
        const data = await apiRequest('/investments/portfolio/allocation/by-currency');
        renderAllocation('allocation-currency', data.allocations, 'alloc-');
    } catch (error) {
        console.error('Failed to load allocation by currency:', error);
    }
}

async function loadAllocationByMarket() {
    try {
        const data = await apiRequest('/investments/portfolio/allocation/by-market');
        renderAllocation('allocation-market', data.allocations, 'alloc-');
    } catch (error) {
        console.error('Failed to load allocation by market:', error);
    }
}

function renderAllocation(containerId, allocations, classPrefix) {
    const container = document.getElementById(containerId);
    
    if (!allocations || allocations.length === 0) {
        container.innerHTML = '<div class="empty-state">No data available</div>';
        return;
    }
    
    container.innerHTML = allocations
        .filter(a => a.percentage > 0)
        .map(a => `
            <div class="allocation-item">
                <span class="allocation-label">${a.name}</span>
                <div class="allocation-bar-container">
                    <div class="allocation-bar ${classPrefix}${a.value.toLowerCase().replace(' ', '-')}" 
                         style="width: ${a.percentage}%"></div>
                </div>
                <span class="allocation-value">${a.percentage.toFixed(1)}%</span>
            </div>
        `).join('');
}

async function loadTopHoldings() {
    try {
        const data = await apiRequest('/investments/portfolio/top-holdings?limit=5');
        const container = document.getElementById('top-holdings');
        
        if (!data.holdings || data.holdings.length === 0) {
            container.innerHTML = '<div class="empty-state">No holdings yet</div>';
            return;
        }
        
        container.innerHTML = data.holdings.map(h => `
            <div class="holding-item">
                <span class="holding-symbol">${h.symbol}</span>
                <span class="holding-name">${h.name}</span>
                <span class="holding-value">${formatCurrency(h.current_value_usd, 'USD')}</span>
                <span class="holding-percent">${h.percentage_of_portfolio.toFixed(1)}%</span>
            </div>
        `).join('');
    } catch (error) {
        console.error('Failed to load top holdings:', error);
    }
}

async function refreshPrices() {
    try {
        showToast('Refreshing prices...', 'info');
        const result = await apiRequest('/investments/assets/refresh-prices', { method: 'POST' });
        showToast(`Updated ${result.updated_count} prices`, 'success');
        loadDashboard();
    } catch (error) {
        showToast(`Failed to refresh prices: ${error.message}`, 'error');
    }
}

// Assets
async function loadAssets() {
    try {
        const assetClass = document.getElementById('filter-asset-class')?.value || '';
        const currency = document.getElementById('filter-currency')?.value || '';
        
        let endpoint = '/investments/assets?';
        if (assetClass) endpoint += `asset_class=${assetClass}&`;
        if (currency) endpoint += `currency=${currency}&`;
        
        const assets = await apiRequest(endpoint);
        renderAssetsTable(assets);
    } catch (error) {
        console.error('Failed to load assets:', error);
        showToast('Failed to load assets', 'error');
    }
}

function renderAssetsTable(assets) {
    const tbody = document.getElementById('assets-table-body');
    
    if (!assets || assets.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="empty-state">
                    No assets found. Click "Add Asset" to create one.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = assets.map(a => `
        <tr>
            <td class="symbol">${a.symbol}</td>
            <td>${a.name}</td>
            <td><span class="badge badge-${a.asset_class}">${a.asset_class.replace('_', ' ')}</span></td>
            <td>${a.asset_type.replace('_', ' ')}</td>
            <td>${a.currency}</td>
            <td>${a.market}</td>
            <td>
                <button class="btn btn-sm btn-secondary" onclick="getAssetPrice(${a.id})">
                    Get Price
                </button>
            </td>
        </tr>
    `).join('');
}

async function getAssetPrice(assetId) {
    try {
        showToast('Fetching price...', 'info');
        const price = await apiRequest(`/investments/assets/${assetId}/price?refresh=true`);
        showToast(`${price.symbol}: ${formatCurrency(price.price, price.currency)} (${price.change_percent?.toFixed(2) || 0}%)`, 'success');
    } catch (error) {
        showToast(`Failed to get price: ${error.message}`, 'error');
    }
}

// Holdings
async function loadHoldings() {
    try {
        const holdings = await apiRequest('/investments/holdings');
        renderHoldingsTable(holdings);
    } catch (error) {
        console.error('Failed to load holdings:', error);
        showToast('Failed to load holdings', 'error');
    }
}

function renderHoldingsTable(holdings) {
    const tbody = document.getElementById('holdings-table-body');
    
    if (!holdings || holdings.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="empty-state">
                    No holdings yet. Click "Add Holding" to create one.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = holdings.map(h => `
        <tr>
            <td class="symbol">${h.symbol || 'N/A'}</td>
            <td>${h.asset_name || 'N/A'}</td>
            <td class="mono">${h.quantity.toFixed(4)}</td>
            <td class="mono">${formatCurrency(h.avg_cost_basis, h.cost_currency)}</td>
            <td class="mono">${h.current_price ? formatCurrency(h.current_price, h.asset_currency) : '-'}</td>
            <td class="mono">${formatCurrency(h.current_value_usd, 'USD')}</td>
            <td class="mono ${h.unrealized_gain_loss >= 0 ? 'positive' : 'negative'}">
                ${formatCurrency(h.unrealized_gain_loss, h.cost_currency)} (${h.unrealized_gain_loss_pct.toFixed(2)}%)
            </td>
            <td>
                <button class="btn btn-sm btn-danger" onclick="deleteHolding(${h.id})">Delete</button>
            </td>
        </tr>
    `).join('');
}

async function deleteHolding(holdingId) {
    if (!confirm('Are you sure you want to delete this holding?')) return;
    
    try {
        await apiRequest(`/investments/holdings/${holdingId}`, { method: 'DELETE' });
        showToast('Holding deleted', 'success');
        loadHoldings();
        loadDashboard();
    } catch (error) {
        showToast(`Failed to delete holding: ${error.message}`, 'error');
    }
}

// Transactions
async function loadTransactions() {
    try {
        const transactions = await apiRequest('/investments/transactions');
        renderTransactionsTable(transactions);
    } catch (error) {
        console.error('Failed to load transactions:', error);
        showToast('Failed to load transactions', 'error');
    }
}

function renderTransactionsTable(transactions) {
    const tbody = document.getElementById('transactions-table-body');
    
    if (!transactions || transactions.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="empty-state">
                    No transactions yet. Click "Record Transaction" to create one.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = transactions.map(t => `
        <tr>
            <td>${new Date(t.executed_at).toLocaleDateString()}</td>
            <td class="symbol">${t.symbol || 'N/A'}</td>
            <td><span class="badge badge-${t.transaction_type}">${t.transaction_type}</span></td>
            <td class="mono">${t.quantity.toFixed(4)}</td>
            <td class="mono">${formatCurrency(t.price_per_unit, t.currency)}</td>
            <td class="mono">${formatCurrency(t.total_amount, t.currency)}</td>
            <td class="mono">${formatCurrency(t.fees, t.currency)}</td>
        </tr>
    `).join('');
}

// Modals
function showAddAssetModal() {
    document.getElementById('add-asset-form').reset();
    openModal('add-asset-modal');
}

async function showAddHoldingModal() {
    // Load assets for dropdown
    try {
        const assets = await apiRequest('/investments/assets');
        const select = document.getElementById('holding-asset');
        select.innerHTML = assets.map(a => 
            `<option value="${a.id}">${a.symbol} - ${a.name}</option>`
        ).join('');
        
        if (assets.length === 0) {
            showToast('Please add some assets first', 'info');
            return;
        }
        
        document.getElementById('add-holding-form').reset();
        openModal('add-holding-modal');
    } catch (error) {
        showToast('Failed to load assets', 'error');
    }
}

async function showAddTransactionModal() {
    // Load holdings for dropdown
    try {
        const holdings = await apiRequest('/investments/holdings');
        const select = document.getElementById('tx-holding');
        select.innerHTML = holdings.map(h => 
            `<option value="${h.id}">${h.symbol} - ${h.asset_name}</option>`
        ).join('');
        
        if (holdings.length === 0) {
            showToast('Please add some holdings first', 'info');
            return;
        }
        
        document.getElementById('add-transaction-form').reset();
        document.getElementById('tx-date').value = new Date().toISOString().slice(0, 16);
        openModal('add-transaction-modal');
    } catch (error) {
        showToast('Failed to load holdings', 'error');
    }
}

function openModal(modalId) {
    document.getElementById('modal-overlay').classList.add('active');
    document.getElementById(modalId).classList.add('active');
}

function closeModal() {
    document.getElementById('modal-overlay').classList.remove('active');
    document.querySelectorAll('.modal').forEach(m => m.classList.remove('active'));
}

// Form Submissions
async function submitAsset(event) {
    event.preventDefault();
    
    const data = {
        symbol: document.getElementById('asset-symbol').value,
        name: document.getElementById('asset-name').value,
        asset_type: document.getElementById('asset-type').value,
        currency: document.getElementById('asset-currency').value,
        market: document.getElementById('asset-market').value,
        country: document.getElementById('asset-country').value,
        sector: document.getElementById('asset-sector').value || null,
    };
    
    try {
        await apiRequest('/investments/assets', {
            method: 'POST',
            body: JSON.stringify(data),
        });
        showToast('Asset created successfully', 'success');
        closeModal();
        loadAssets();
    } catch (error) {
        showToast(`Failed to create asset: ${error.message}`, 'error');
    }
}

async function submitHolding(event) {
    event.preventDefault();
    
    const data = {
        asset_id: parseInt(document.getElementById('holding-asset').value),
        quantity: parseFloat(document.getElementById('holding-quantity').value),
        avg_cost_basis: parseFloat(document.getElementById('holding-cost').value),
        cost_currency: document.getElementById('holding-currency').value,
    };
    
    try {
        await apiRequest('/investments/holdings', {
            method: 'POST',
            body: JSON.stringify(data),
        });
        showToast('Holding created successfully', 'success');
        closeModal();
        loadHoldings();
        loadDashboard();
    } catch (error) {
        showToast(`Failed to create holding: ${error.message}`, 'error');
    }
}

async function submitTransaction(event) {
    event.preventDefault();
    
    const data = {
        holding_id: parseInt(document.getElementById('tx-holding').value),
        transaction_type: document.getElementById('tx-type').value,
        quantity: parseFloat(document.getElementById('tx-quantity').value),
        price_per_unit: parseFloat(document.getElementById('tx-price').value),
        currency: document.getElementById('tx-currency').value,
        fees: parseFloat(document.getElementById('tx-fees').value) || 0,
        executed_at: new Date(document.getElementById('tx-date').value).toISOString(),
        broker: document.getElementById('tx-broker').value || null,
        notes: document.getElementById('tx-notes').value || null,
    };
    
    try {
        await apiRequest('/investments/transactions', {
            method: 'POST',
            body: JSON.stringify(data),
        });
        showToast('Transaction recorded successfully', 'success');
        closeModal();
        loadTransactions();
        loadHoldings();
        loadDashboard();
    } catch (error) {
        showToast(`Failed to record transaction: ${error.message}`, 'error');
    }
}

// Utilities
function formatCurrency(amount, currency = 'USD') {
    if (amount === null || amount === undefined) return '-';
    
    const formatter = new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    
    return formatter.format(amount);
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeModal();
    }
});

