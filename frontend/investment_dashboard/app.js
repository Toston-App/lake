// Configuration
const API_BASE = 'http://localhost:8888/api/v1';
let authToken = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NzgyMDE5MjgsInVzZXIiOnsibmFtZSI6InN0cmluZyIsImVtYWlsIjoidXNlcjNAZXhhbXBsZS5jb20iLCJjb3VudHJ5Ijoic3RyaW5nIiwiaWQiOjZ9fQ.g7tdYwwQz4CmQjogMNZhDv2n8G2ShYLGOfr-8OfLJSY"; // Set your auth token here

// State
let accountsList = [];

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
        case 'accounts':
            loadAccounts();
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
        loadAccountsList();
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
        
        // Combined total invested (all investments converted to single currency)
        document.getElementById('total-invested-combined-usd').textContent = formatCurrency(summary.total_invested_combined_usd, 'USD');
        document.getElementById('total-invested-combined-mxn').textContent = formatCurrency(summary.total_invested_combined_mxn, 'MXN');
        
        // Show original currency breakdown as sub-text
        document.getElementById('total-invested-usd-only').textContent = `USD: ${formatCurrency(summary.total_invested_usd, 'USD')}`;
        document.getElementById('total-invested-mxn-only').textContent = `MXN: ${formatCurrency(summary.total_invested_mxn, 'MXN')}`;
        
        document.getElementById('total-gain-loss').textContent = formatCurrency(summary.total_gain_loss, 'USD');
        
        const changeEl = document.getElementById('total-change');
        changeEl.textContent = `${summary.total_gain_loss_pct >= 0 ? '+' : ''}${summary.total_gain_loss_pct.toFixed(2)}%`;
        changeEl.className = `card-change ${summary.total_gain_loss_pct >= 0 ? 'positive' : 'negative'}`;
        
        // Load allocations
        loadAllocationByClass();
        loadAllocationByCurrency();
        loadAllocationByMarket();
        loadAllocationByAccount();
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

async function loadAllocationByAccount() {
    try {
        const data = await apiRequest('/investments/portfolio/allocation/by-account');
        renderAccountAllocation('allocation-account', data.allocations);
    } catch (error) {
        console.error('Failed to load allocation by account:', error);
    }
}

function renderAccountAllocation(containerId, allocations) {
    const container = document.getElementById(containerId);
    
    if (!allocations || allocations.length === 0) {
        container.innerHTML = '<div class="empty-state">No data available</div>';
        return;
    }
    
    container.innerHTML = allocations
        .filter(a => a.percentage > 0)
        .map(a => {
            const firstLetter = (a.name || a.value || 'U').charAt(0).toUpperCase();
            const accountKey = (a.value || 'unknown').toLowerCase().replace(/[^a-z0-9]/g, '-');
            
            return `
                <div class="allocation-item allocation-item-with-logo">
                    <div class="broker-logo-wrapper-sm">
                        <div class="broker-logo-fallback-sm" style="display: flex;">${firstLetter}</div>
                    </div>
                    <span class="allocation-label">${a.name}</span>
                    <div class="allocation-bar-container">
                        <div class="allocation-bar alloc-account-${accountKey}" 
                             style="width: ${a.percentage}%; background: linear-gradient(90deg, var(--accent-primary), var(--accent-secondary));"></div>
                    </div>
                    <span class="allocation-value">${a.percentage.toFixed(1)}%</span>
                </div>
            `;
        }).join('');
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
    // Ensure accounts are loaded
    if (accountsList.length === 0) {
        await loadAccountsList();
    }

    // Load assets for dropdown
    try {
        const assets = await apiRequest('/investments/assets');
        const assetSelect = document.getElementById('holding-asset');
        assetSelect.innerHTML = assets.map(a => 
            `<option value="${a.id}">${a.symbol} - ${a.name}</option>`
        ).join('');
        
        if (assets.length === 0) {
            showToast('Please add some assets first', 'info');
            return;
        }
        
        populateAccountSelects();
        document.getElementById('add-holding-form').reset();
        document.getElementById('holding-new-account-fields').style.display = 'none';
        document.getElementById('holding-new-account-name').required = false;

        if (accountsList.length === 0) {
            document.getElementById('holding-account').value = '__new__';
            toggleNewAccountFields('holding');
        }

        openModal('add-holding-modal');
    } catch (error) {
        showToast('Failed to load assets', 'error');
    }
}

// Asset search state
let searchTimeout = null;
let selectedAsset = null;

// Load accounts on init
async function loadAccountsList() {
    try {
        accountsList = await apiRequest('/accounts');
        populateAccountSelects();
    } catch (error) {
        console.error('Failed to load accounts list:', error);
    }
}

function populateAccountSelects() {
    // Populate transaction modal account select
    const txAccountSelect = document.getElementById('tx-account');
    if (txAccountSelect) {
        txAccountSelect.innerHTML = accountsList.map(a =>
            `<option value="${a.id}">${a.name}</option>`
        ).join('');
    }

    // Populate holding modal account select (with "Create New" option)
    const holdingAccountSelect = document.getElementById('holding-account');
    if (holdingAccountSelect) {
        const accountOptions = accountsList.map(a =>
            `<option value="${a.id}">${a.name}</option>`
        ).join('');
        holdingAccountSelect.innerHTML = accountOptions +
            '<option value="__new__">+ Create New Account</option>';
    }
}

function toggleNewAccountFields(prefix) {
    const select = document.getElementById(`${prefix}-account`);
    const fields = document.getElementById(`${prefix}-new-account-fields`);
    const nameInput = document.getElementById(`${prefix}-new-account-name`);

    if (select.value === '__new__') {
        fields.style.display = 'block';
        nameInput.required = true;
    } else {
        fields.style.display = 'none';
        nameInput.required = false;
    }
}

// Accounts
async function loadAccounts() {
    try {
        const accounts = await apiRequest('/accounts');
        accountsList = accounts;
        renderAccountsTable(accounts);
    } catch (error) {
        console.error('Failed to load accounts:', error);
        showToast('Failed to load accounts', 'error');
    }
}

function renderAccountsTable(accounts) {
    const tbody = document.getElementById('accounts-table-body');
    
    if (!accounts || accounts.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="empty-state">
                    No accounts yet. Click "Add Account" to create one.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = accounts.map(a => `
        <tr>
            <td>
                <div class="account-name-cell">
                    <span class="account-color-dot" style="background: ${a.color || '#6366f1'}"></span>
                    ${a.name}
                </div>
            </td>
            <td><span class="badge">${a.type}</span></td>
            <td class="mono">${formatCurrency(a.initial_balance, 'USD')}</td>
            <td class="mono">${formatCurrency(a.current_balance, 'USD')}</td>
            <td>
                <span class="account-color-swatch" style="background: ${a.color || '#6366f1'}"></span>
            </td>
            <td>
                <button class="btn btn-sm btn-danger" onclick="deleteAccount(${a.id})">Delete</button>
            </td>
        </tr>
    `).join('');
}

function showAddAccountModal() {
    document.getElementById('add-account-form').reset();
    document.getElementById('account-color').value = '#6366f1';
    document.getElementById('account-color-hex').textContent = '#6366f1';
    
    const colorInput = document.getElementById('account-color');
    colorInput.addEventListener('input', () => {
        document.getElementById('account-color-hex').textContent = colorInput.value;
    });
    
    openModal('add-account-modal');
}

async function submitAccount(event) {
    event.preventDefault();
    
    const initialBalance = parseFloat(document.getElementById('account-initial-balance').value) || 0;
    const data = {
        name: document.getElementById('account-name').value,
        type: document.getElementById('account-type').value,
        initial_balance: initialBalance,
        current_balance: initialBalance,
        color: document.getElementById('account-color').value,
    };
    
    try {
        await apiRequest('/accounts', {
            method: 'POST',
            body: JSON.stringify(data),
        });
        showToast('Account created successfully', 'success');
        closeModal();
        loadAccounts();
        loadAccountsList();
    } catch (error) {
        showToast(`Failed to create account: ${error.message}`, 'error');
    }
}

async function deleteAccount(accountId) {
    if (!confirm('Are you sure you want to delete this account? All associated holdings and transactions will also be deleted.')) return;
    
    try {
        await apiRequest(`/accounts/${accountId}`, { method: 'DELETE' });
        showToast('Account deleted', 'success');
        loadAccounts();
        loadAccountsList();
    } catch (error) {
        showToast(`Failed to delete account: ${error.message}`, 'error');
    }
}

async function showAddTransactionModal() {
    // Ensure accounts are loaded
    if (accountsList.length === 0) {
        await loadAccountsList();
    }
    if (accountsList.length === 0) {
        showToast('Please create an account first', 'info');
        return;
    }

    // Reset form and state
    document.getElementById('add-transaction-form').reset();
    document.getElementById('tx-date').value = new Date().toISOString().slice(0, 16);
    
    // Reset asset selection
    selectedAsset = null;
    document.getElementById('selected-asset-display').style.display = 'none';
    document.getElementById('tx-asset-search').style.display = 'block';
    document.getElementById('tx-asset-search').value = '';
    document.getElementById('asset-search-results').innerHTML = '';
    document.getElementById('tx-manual-entry').checked = false;
    document.getElementById('manual-asset-fields').style.display = 'none';
    
    // Re-populate account dropdown
    populateAccountSelects();
    
    // Clear hidden fields
    document.getElementById('tx-selected-symbol').value = '';
    document.getElementById('tx-selected-name').value = '';
    document.getElementById('tx-selected-type').value = '';
    document.getElementById('tx-selected-market').value = '';
    document.getElementById('tx-selected-currency').value = '';
    document.getElementById('tx-selected-country').value = '';
    document.getElementById('tx-selected-coingecko-id').value = '';
    document.getElementById('tx-selected-provider').value = '';
    document.getElementById('tx-selected-external-id').value = '';
    
    openModal('add-transaction-modal');
}

async function searchAssets(query) {
    const resultsContainer = document.getElementById('asset-search-results');
    
    if (!query || query.length < 1) {
        resultsContainer.innerHTML = '';
        return;
    }
    
    // Debounce search
    if (searchTimeout) {
        clearTimeout(searchTimeout);
    }
    
    searchTimeout = setTimeout(async () => {
        try {
            resultsContainer.innerHTML = '<div class="search-loading">Searching...</div>';
            
            // Call both endpoints in parallel
            const [stockResults, cryptoResults] = await Promise.all([
                apiRequest(`/investments/assets/search-external?q=${encodeURIComponent(query)}`).catch(() => []),
                apiRequest(`/investments/assets/search-crypto?q=${encodeURIComponent(query)}`).catch(() => [])
            ]);
            
            // Combine results
            const allResults = [...cryptoResults, ...stockResults];
            
            if (allResults.length === 0) {
                resultsContainer.innerHTML = '<div class="search-no-results">No assets found. Try manual entry.</div>';
                return;
            }
            
            // Group results by type for better display
            const cryptoGroup = cryptoResults.length > 0 ? `
                <div class="search-group">
                    <div class="search-group-header">Cryptocurrencies</div>
                    ${cryptoResults.map(asset => `
                        <div class="search-result-item" onclick='selectCryptoAsset(${JSON.stringify(asset)})'>
                            <span class="result-symbol">${asset.symbol}</span>
                            <span class="result-name">${asset.name}</span>
                            <span class="result-market badge badge-crypto">CRYPTO</span>
                        </div>
                    `).join('')}
                </div>
            ` : '';
            
            const stockGroup = stockResults.length > 0 ? `
                <div class="search-group">
                    <div class="search-group-header">Stocks & ETFs</div>
                    ${stockResults.map(asset => `
                        <div class="search-result-item" onclick='selectAsset(${JSON.stringify(asset)})'>
                            <span class="result-symbol">${asset.symbol}</span>
                            <span class="result-name">${asset.name}</span>
                            <span class="result-market badge badge-${asset.market.toLowerCase()}">${asset.market}</span>
                        </div>
                    `).join('')}
                </div>
            ` : '';
            
            resultsContainer.innerHTML = cryptoGroup + stockGroup;
            
        } catch (error) {
            console.error('Asset search failed:', error);
            resultsContainer.innerHTML = '<div class="search-error">Search failed. Try manual entry.</div>';
        }
    }, 300);
}

function selectAsset(asset) {
    selectedAsset = asset;
    
    // Update hidden fields
    document.getElementById('tx-selected-symbol').value = asset.symbol;
    document.getElementById('tx-selected-name').value = asset.name;
    document.getElementById('tx-selected-type').value = asset.asset_type;
    document.getElementById('tx-selected-market').value = asset.market;
    document.getElementById('tx-selected-currency').value = asset.currency;
    document.getElementById('tx-selected-country').value = asset.country;
    document.getElementById('tx-selected-coingecko-id').value = '';
    document.getElementById('tx-selected-provider').value = asset.provider || '';
    document.getElementById('tx-selected-external-id').value = asset.external_id || '';
    
    // Update display
    document.getElementById('selected-asset-symbol').textContent = asset.symbol;
    document.getElementById('selected-asset-name').textContent = asset.name;
    document.getElementById('selected-asset-market').textContent = asset.market;
    document.getElementById('selected-asset-market').className = `asset-market badge badge-${asset.market.toLowerCase()}`;
    
    // Show selected asset, hide search
    document.getElementById('selected-asset-display').style.display = 'flex';
    document.getElementById('tx-asset-search').style.display = 'none';
    document.getElementById('asset-search-results').innerHTML = '';
    
    // Update currency based on asset
    document.getElementById('tx-currency').value = asset.currency;
    
    // Hide manual entry if it was open
    document.getElementById('tx-manual-entry').checked = false;
    document.getElementById('manual-asset-fields').style.display = 'none';
}

function selectCryptoAsset(asset) {
    selectedAsset = asset;
    
    // Update hidden fields (crypto doesn't have country, but has coingecko_id)
    document.getElementById('tx-selected-symbol').value = asset.symbol;
    document.getElementById('tx-selected-name').value = asset.name;
    document.getElementById('tx-selected-type').value = asset.asset_type;
    document.getElementById('tx-selected-market').value = asset.market;
    document.getElementById('tx-selected-currency').value = asset.currency;
    document.getElementById('tx-selected-country').value = 'GLOBAL';
    document.getElementById('tx-selected-coingecko-id').value = asset.coingecko_id || '';
    document.getElementById('tx-selected-provider').value = asset.provider || '';
    document.getElementById('tx-selected-external-id').value = asset.external_id || '';
    
    // Update display
    document.getElementById('selected-asset-symbol').textContent = asset.symbol;
    document.getElementById('selected-asset-name').textContent = asset.name;
    document.getElementById('selected-asset-market').textContent = asset.market;
    document.getElementById('selected-asset-market').className = `asset-market badge badge-crypto`;
    
    // Show selected asset, hide search
    document.getElementById('selected-asset-display').style.display = 'flex';
    document.getElementById('tx-asset-search').style.display = 'none';
    document.getElementById('asset-search-results').innerHTML = '';
    
    // Update currency based on asset (crypto defaults to USD)
    document.getElementById('tx-currency').value = asset.currency;
    
    // Hide manual entry if it was open
    document.getElementById('tx-manual-entry').checked = false;
    document.getElementById('manual-asset-fields').style.display = 'none';
}

function clearSelectedAsset() {
    selectedAsset = null;
    
    // Clear hidden fields
    document.getElementById('tx-selected-symbol').value = '';
    document.getElementById('tx-selected-name').value = '';
    document.getElementById('tx-selected-type').value = '';
    document.getElementById('tx-selected-market').value = '';
    document.getElementById('tx-selected-currency').value = '';
    document.getElementById('tx-selected-country').value = '';
    document.getElementById('tx-selected-coingecko-id').value = '';
    document.getElementById('tx-selected-provider').value = '';
    document.getElementById('tx-selected-external-id').value = '';
    
    // Show search, hide selected
    document.getElementById('selected-asset-display').style.display = 'none';
    document.getElementById('tx-asset-search').style.display = 'block';
    document.getElementById('tx-asset-search').value = '';
    document.getElementById('tx-asset-search').focus();
}

function toggleManualEntry() {
    const isManual = document.getElementById('tx-manual-entry').checked;
    const manualFields = document.getElementById('manual-asset-fields');
    const searchContainer = document.getElementById('tx-asset-search').parentElement;
    
    if (isManual) {
        manualFields.style.display = 'block';
        document.getElementById('tx-asset-search').style.display = 'none';
        document.getElementById('asset-search-results').innerHTML = '';
        document.getElementById('selected-asset-display').style.display = 'none';
        selectedAsset = null;
    } else {
        manualFields.style.display = 'none';
        document.getElementById('tx-asset-search').style.display = 'block';
    }
}

function updateCurrencyFromMarket() {
    const market = document.getElementById('tx-market').value;
    const currencySelect = document.getElementById('tx-currency');
    
    if (market === 'BMV') {
        currencySelect.value = 'MXN';
    } else {
        currencySelect.value = 'USD';
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
    
    let accountId;
    const accountSelect = document.getElementById('holding-account').value;

    if (accountSelect === '__new__') {
        const accountName = document.getElementById('holding-new-account-name').value.trim();
        if (!accountName) {
            showToast('Please enter a name for the new account', 'error');
            return;
        }
        try {
            const newAccount = await apiRequest('/accounts', {
                method: 'POST',
                body: JSON.stringify({
                    name: accountName,
                    type: document.getElementById('holding-new-account-type').value,
                    initial_balance: 0,
                    current_balance: 0,
                    color: document.getElementById('holding-new-account-color').value,
                }),
            });
            accountId = newAccount.id;
            await loadAccountsList();
        } catch (error) {
            showToast(`Failed to create account: ${error.message}`, 'error');
            return;
        }
    } else {
        accountId = parseInt(accountSelect);
    }

    const data = {
        asset_id: parseInt(document.getElementById('holding-asset').value),
        account_id: accountId,
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
    
    const isManualEntry = document.getElementById('tx-manual-entry').checked;
    
    let provider, externalId, assetId, symbol;
    
    if (isManualEntry) {
        symbol = document.getElementById('tx-symbol').value.toUpperCase().trim();
        if (!symbol) {
            showToast('Please enter an asset symbol', 'error');
            return;
        }
        // For manual entry, create the asset first then use asset_id
        const assetData = {
            symbol: symbol,
            name: document.getElementById('tx-asset-name').value || symbol,
            asset_type: document.getElementById('tx-asset-type').value,
            market: document.getElementById('tx-market').value,
            currency: document.getElementById('tx-currency').value,
            country: document.getElementById('tx-market').value === 'BMV' ? 'MX' : 'US',
        };
        try {
            const asset = await apiRequest('/investments/assets', {
                method: 'POST',
                body: JSON.stringify(assetData),
            });
            assetId = asset.id;
        } catch (error) {
            if (error.message && error.message.includes('already exists')) {
                // Asset exists, look it up
                const assets = await apiRequest(`/investments/assets?symbol=${encodeURIComponent(symbol)}`);
                const match = Array.isArray(assets) ? assets.find(a => a.symbol === symbol) : null;
                if (match) {
                    assetId = match.id;
                } else {
                    showToast(`Failed to find existing asset: ${symbol}`, 'error');
                    return;
                }
            } else {
                showToast(`Failed to create asset: ${error.message}`, 'error');
                return;
            }
        }
    } else if (selectedAsset) {
        symbol = selectedAsset.symbol;
        provider = selectedAsset.provider || null;
        externalId = selectedAsset.external_id || null;
    } else {
        // Check hidden fields (for form resubmission)
        symbol = document.getElementById('tx-selected-symbol').value;
        if (!symbol) {
            showToast('Please select or enter an asset', 'error');
            return;
        }
        provider = document.getElementById('tx-selected-provider').value || null;
        externalId = document.getElementById('tx-selected-external-id').value || null;
    }
    
    if (!symbol) {
        showToast('Please select or enter an asset symbol', 'error');
        return;
    }
    
    const accountId = parseInt(document.getElementById('tx-account').value);
    if (!accountId) {
        showToast('Please select an account', 'error');
        return;
    }

    const data = {
        transaction_type: document.getElementById('tx-type').value,
        quantity: parseFloat(document.getElementById('tx-quantity').value),
        price_per_unit: parseFloat(document.getElementById('tx-price').value),
        fees: parseFloat(document.getElementById('tx-fees').value) || 0,
        executed_at: new Date(document.getElementById('tx-date').value).toISOString(),
        account_id: accountId,
        notes: document.getElementById('tx-notes').value || null,
    };

    if (assetId) {
        data.asset_id = assetId;
    } else if (provider && externalId) {
        data.provider = provider;
        data.external_id = externalId;
    } else {
        showToast('Missing asset identity. Please search and select an asset, or use manual entry.', 'error');
        return;
    }
    
    try {
        const result = await apiRequest('/investments/transactions/with-asset', {
            method: 'POST',
            body: JSON.stringify(data),
        });
        
        let message = 'Transaction recorded successfully';
        if (result.asset_created) {
            message += ` (created new asset: ${symbol})`;
        }
        if (result.holding_created) {
            message += ' with new holding';
        }
        
        showToast(message, 'success');
        closeModal();
        loadTransactions();
        loadHoldings();
        loadAssets();
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
