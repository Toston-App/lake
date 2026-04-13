// Configuration
const API_BASE = 'http://localhost:8888/api/v1';
let authToken = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NzYwNTUzNTYsInVzZXIiOnsibmFtZSI6InN0cmluZyIsImVtYWlsIjoidXNlckBleGFtcGxlLmNvbSIsImNvdW50cnkiOiJzdHJpbmciLCJpZCI6Mn19.hPWJkiyjqjGgwBZn6hSHj-msKp5G27XhM9KTvQ213bU"; // Set your auth token here

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
        loadBrokersList();
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
        loadAllocationByBroker();
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

async function loadAllocationByBroker() {
    try {
        const data = await apiRequest('/investments/portfolio/allocation/by-broker');
        renderBrokerAllocation('allocation-broker', data.allocations);
    } catch (error) {
        console.error('Failed to load allocation by broker:', error);
    }
}

function renderBrokerAllocation(containerId, allocations) {
    const container = document.getElementById(containerId);
    
    if (!allocations || allocations.length === 0) {
        container.innerHTML = '<div class="empty-state">No data available</div>';
        return;
    }
    
    container.innerHTML = allocations
        .filter(a => a.percentage > 0)
        .map(a => {
            const firstLetter = (a.name || a.value || 'U').charAt(0).toUpperCase();
            const logoUrl = a.logo_url || '';
            const brokerKey = (a.value || 'unknown').toLowerCase().replace(/[^a-z0-9]/g, '-');
            
            return `
                <div class="allocation-item allocation-item-with-logo">
                    <div class="broker-logo-wrapper-sm">
                        <img src="${logoUrl}" 
                             class="broker-logo-sm" 
                             onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" 
                             alt="">
                        <div class="broker-logo-fallback-sm" style="${logoUrl ? 'display: none;' : 'display: flex;'}">${firstLetter}</div>
                    </div>
                    <span class="allocation-label">${a.name}</span>
                    <div class="allocation-bar-container">
                        <div class="allocation-bar alloc-broker-${brokerKey}" 
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

// Asset search state
let searchTimeout = null;
let selectedAsset = null;

// Broker state
let brokersList = [];
let brokersGrouped = {};
let selectedBroker = null;
let brokerSearchTimeout = null;

// Load brokers on init
async function loadBrokersList() {
    try {
        const response = await apiRequest('/brokers/grouped');
        brokersGrouped = response.groups;
        // Flatten for easy searching
        brokersList = [
            ...(response.groups.US || []),
            ...(response.groups.Mexico || []),
            ...(response.groups.Crypto || []),
            ...(response.groups.International || []),
        ];
    } catch (error) {
        console.error('Failed to load brokers list:', error);
    }
}

// Search brokers (local filtering with category grouping)
function searchBrokers(query) {
    const resultsContainer = document.getElementById('broker-search-results');
    
    if (!query || query.length < 1) {
        resultsContainer.innerHTML = '';
        return;
    }
    
    // Debounce
    if (brokerSearchTimeout) {
        clearTimeout(brokerSearchTimeout);
    }
    
    brokerSearchTimeout = setTimeout(() => {
        const queryLower = query.toLowerCase();
        
        // Filter and group results
        const grouped = {
            US: (brokersGrouped.US || []).filter(b => 
                b.name.toLowerCase().includes(queryLower) || 
                b.code.toLowerCase().includes(queryLower)
            ),
            Mexico: (brokersGrouped.Mexico || []).filter(b => 
                b.name.toLowerCase().includes(queryLower) || 
                b.code.toLowerCase().includes(queryLower)
            ),
            Crypto: (brokersGrouped.Crypto || []).filter(b => 
                b.name.toLowerCase().includes(queryLower) || 
                b.code.toLowerCase().includes(queryLower)
            ),
            International: (brokersGrouped.International || []).filter(b => 
                b.name.toLowerCase().includes(queryLower) || 
                b.code.toLowerCase().includes(queryLower)
            ),
        };
        
        const totalMatches = grouped.US.length + grouped.Mexico.length + 
                            grouped.Crypto.length + grouped.International.length;
        
        if (totalMatches === 0) {
            resultsContainer.innerHTML = '<div class="search-no-results">No brokers found</div>';
            return;
        }
        
        let html = '';
        
        if (grouped.US.length > 0) {
            html += '<div class="search-group-header">United States</div>';
            html += grouped.US.map(b => brokerResultItem(b)).join('');
        }
        if (grouped.Mexico.length > 0) {
            html += '<div class="search-group-header">Mexico</div>';
            html += grouped.Mexico.map(b => brokerResultItem(b)).join('');
        }
        if (grouped.Crypto.length > 0) {
            html += '<div class="search-group-header">Crypto Exchanges</div>';
            html += grouped.Crypto.map(b => brokerResultItem(b)).join('');
        }
        if (grouped.International.length > 0) {
            html += '<div class="search-group-header">International</div>';
            html += grouped.International.map(b => brokerResultItem(b)).join('');
        }
        
        resultsContainer.innerHTML = html;
    }, 150);
}

function brokerResultItem(broker) {
    const firstLetter = broker.name.charAt(0).toUpperCase();
    const brokerJson = JSON.stringify(broker).replace(/'/g, "\\'");
    return `
        <div class="search-result-item" onclick='selectBroker(${brokerJson})'>
            <div class="broker-logo-wrapper-sm">
                <img src="${broker.logo_url || ''}" 
                     class="broker-logo-sm" 
                     onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" 
                     alt="">
                <div class="broker-logo-fallback-sm" style="display: none;">${firstLetter}</div>
            </div>
            <span class="result-name">${broker.name}</span>
            <span class="result-market badge badge-${broker.country.toLowerCase()}">${broker.country}</span>
        </div>
    `;
}

function selectBroker(broker) {
    selectedBroker = broker;
    
    // Update hidden field
    document.getElementById('tx-selected-broker').value = broker.code;
    
    // Update display
    const logo = document.getElementById('selected-broker-logo');
    const fallback = document.getElementById('selected-broker-fallback');
    
    logo.src = broker.logo_url || '';
    logo.style.display = 'block';
    fallback.style.display = 'none';
    fallback.textContent = broker.name.charAt(0).toUpperCase();
    
    // Handle logo error
    logo.onerror = function() {
        this.style.display = 'none';
        fallback.style.display = 'flex';
    };
    
    document.getElementById('selected-broker-name').textContent = broker.name;
    document.getElementById('selected-broker-country').textContent = broker.country;
    document.getElementById('selected-broker-country').className = 
        `asset-market badge badge-${broker.country.toLowerCase()}`;
    
    // Show selected, hide search
    document.getElementById('selected-broker-display').style.display = 'flex';
    document.getElementById('tx-broker-search').style.display = 'none';
    document.getElementById('broker-search-results').innerHTML = '';
}

function clearSelectedBroker() {
    selectedBroker = null;
    document.getElementById('tx-selected-broker').value = '';
    
    // Show search, hide selected
    document.getElementById('selected-broker-display').style.display = 'none';
    document.getElementById('tx-broker-search').style.display = 'block';
    document.getElementById('tx-broker-search').value = '';
    document.getElementById('tx-broker-search').focus();
}

async function showAddTransactionModal() {
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
    
    // Reset broker selection
    selectedBroker = null;
    document.getElementById('tx-selected-broker').value = '';
    document.getElementById('selected-broker-display').style.display = 'none';
    document.getElementById('tx-broker-search').style.display = 'block';
    document.getElementById('tx-broker-search').value = '';
    document.getElementById('broker-search-results').innerHTML = '';
    
    // Clear hidden fields
    document.getElementById('tx-selected-symbol').value = '';
    document.getElementById('tx-selected-name').value = '';
    document.getElementById('tx-selected-type').value = '';
    document.getElementById('tx-selected-market').value = '';
    document.getElementById('tx-selected-currency').value = '';
    document.getElementById('tx-selected-country').value = '';
    document.getElementById('tx-selected-coingecko-id').value = '';
    
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
    document.getElementById('tx-selected-country').value = 'GLOBAL'; // Crypto is global
    document.getElementById('tx-selected-coingecko-id').value = asset.coingecko_id || '';
    
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
    
    const isManualEntry = document.getElementById('tx-manual-entry').checked;
    
    // Get asset info (from selection or manual entry)
    let symbol, assetName, assetType, market, currency, country, coingeckoId;
    
    if (isManualEntry) {
        symbol = document.getElementById('tx-symbol').value.toUpperCase().trim();
        assetName = document.getElementById('tx-asset-name').value || symbol;
        assetType = document.getElementById('tx-asset-type').value;
        market = document.getElementById('tx-market').value;
        currency = document.getElementById('tx-currency').value;
        country = market === 'BMV' ? 'MX' : 'US';
        coingeckoId = null;
    } else if (selectedAsset) {
        symbol = selectedAsset.symbol;
        assetName = selectedAsset.name;
        assetType = selectedAsset.asset_type;
        market = selectedAsset.market;
        currency = selectedAsset.currency;
        country = selectedAsset.country;
        coingeckoId = selectedAsset.coingecko_id || null;
    } else {
        // Check hidden fields (for form resubmission)
        symbol = document.getElementById('tx-selected-symbol').value;
        if (!symbol) {
            showToast('Please select or enter an asset', 'error');
            return;
        }
        assetName = document.getElementById('tx-selected-name').value || symbol;
        assetType = document.getElementById('tx-selected-type').value || 'stock';
        market = document.getElementById('tx-selected-market').value || 'NYSE';
        currency = document.getElementById('tx-selected-currency').value || 'USD';
        country = document.getElementById('tx-selected-country').value || 'US';
        coingeckoId = document.getElementById('tx-selected-coingecko-id').value || null;
    }
    
    if (!symbol) {
        showToast('Please select or enter an asset symbol', 'error');
        return;
    }
    
    const data = {
        symbol: symbol,
        asset_name: assetName,
        asset_type: assetType,
        market: market,
        currency: currency,
        country: country,
        coingecko_id: coingeckoId,
        transaction_type: document.getElementById('tx-type').value,
        quantity: parseFloat(document.getElementById('tx-quantity').value),
        price_per_unit: parseFloat(document.getElementById('tx-price').value),
        fees: parseFloat(document.getElementById('tx-fees').value) || 0,
        executed_at: new Date(document.getElementById('tx-date').value).toISOString(),
        broker: document.getElementById('tx-selected-broker').value || null,
        notes: document.getElementById('tx-notes').value || null,
    };
    
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
