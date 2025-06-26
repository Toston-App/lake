# Financial Analytics Chatbot

A powerful financial analytics chatbot built with PydanticAI and FastAPI, designed to integrate seamlessly with Vercel AI SDK.

## Features

### 📊 Financial Analytics & Insights
- **Spending Analysis**: "Show me my spending for this month" / "What did I spend most on last week?"
- **Categories Tracking**: "How much have I spent on groceries this month?"
- **Trend Analysis**: "Show me my spending trends over the last 3 months" / "Which categories are increasing?"
- **Account Balances**: "What's my current balance across all accounts?" / "Show me my savings account balance"

### 💰 Transaction Management
- **Quick Expense Entry**: "Add $25 for lunch at McDonald's" / "Record $150 gas expense"
- **Income Recording**: "Add $2000 salary deposit" / "Record $500 freelance payment"
- **Transfer Tracking**: "Transfer $500 from checking to savings" / "Move $100 to crypto wallet"

## API Endpoints

### 1. Chat Message Endpoint
```http
POST /api/v1/chat/message
```

**Request Body:**
```json
{
  "message": "Show me my spending for this month",
  "user_id": 123
}
```

**Response:**
```json
{
  "response": "Based on your spending analysis for this month...",
  "success": true,
  "error": null
}
```

### 2. Streaming Chat Endpoint
```http
POST /api/v1/chat/stream
```

Returns a Server-Sent Events (SSE) stream for real-time chat responses.

### 3. Transaction Creation Endpoint
```http
POST /api/v1/chat/transaction
```

**Request Body:**
```json
{
  "message": "Add $25 for lunch at McDonald's",
  "user_id": 123
}
```

**Response:**
```json
{
  "success": true,
  "message": "✅ Expense recorded: $25.00 for lunch at McDonald's",
  "transaction": {
    "type": "expense",
    "id": 456,
    "amount": 25.0,
    "description": "lunch at McDonald's",
    "date": "2024-01-15",
    "account": "Checking Account",
    "category": "Food",
    "place": "McDonald's"
  },
  "transaction_type": "expense"
}
```

### 4. Capabilities Endpoint
```http
GET /api/v1/chat/capabilities
```

Returns information about what the chatbot can do.

### 5. Test Endpoints
```http
POST /api/v1/chat/test
POST /api/v1/chat/test-transaction
```

Test the chat and transaction functionality with sample messages.

## Frontend Integration with Vercel AI SDK

### Basic Usage

```typescript
import { useChat } from 'ai/react'

export default function ChatComponent() {
  const { messages, input, handleInputChange, handleSubmit, isLoading } = useChat({
    api: '/api/v1/chat/message',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  })

  return (
    <div>
      <div className="messages">
        {messages.map((message) => (
          <div key={message.id}>
            {message.role === 'user' ? 'You: ' : 'Assistant: '}
            {message.content}
          </div>
        ))}
      </div>
      
      <form onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={handleInputChange}
          placeholder="Ask about your finances or add transactions..."
          disabled={isLoading}
        />
        <button type="submit" disabled={isLoading}>
          Send
        </button>
      </form>
    </div>
  )
}
```

### Transaction Creation Component

```typescript
import { useState } from 'react'

export default function TransactionComponent() {
  const [message, setMessage] = useState('')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)

  const createTransaction = async () => {
    setLoading(true)
    try {
      const response = await fetch('/api/v1/chat/transaction', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: message,
          user_id: currentUser.id,
        }),
      })
      
      const data = await response.json()
      setResult(data)
    } catch (error) {
      console.error('Error creating transaction:', error)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <input
        type="text"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder="Add $25 for lunch at McDonald's"
      />
      <button onClick={createTransaction} disabled={loading}>
        {loading ? 'Creating...' : 'Add Transaction'}
      </button>
      
      {result && (
        <div className={result.success ? 'success' : 'error'}>
          {result.message}
        </div>
      )}
    </div>
  )
}
```

### Streaming Usage

```typescript
import { useChat } from 'ai/react'

export default function StreamingChatComponent() {
  const { messages, input, handleInputChange, handleSubmit, isLoading } = useChat({
    api: '/api/v1/chat/stream',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  })

  return (
    <div>
      <div className="messages">
        {messages.map((message) => (
          <div key={message.id}>
            {message.role === 'user' ? 'You: ' : 'Assistant: '}
            {message.content}
          </div>
        ))}
      </div>
      
      <form onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={handleInputChange}
          placeholder="Ask about your finances or add transactions..."
          disabled={isLoading}
        />
        <button type="submit" disabled={isLoading}>
          Send
        </button>
      </form>
    </div>
  )
}
```

### Custom API Route (Next.js)

Create a custom API route to handle authentication and proxy requests:

```typescript
// pages/api/chat.ts
import type { NextApiRequest, NextApiResponse } from 'next'

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse
) {
  if (req.method !== 'POST') {
    return res.status(405).json({ message: 'Method not allowed' })
  }

  try {
    const { messages } = req.body
    
    // Get the last user message
    const lastMessage = messages[messages.length - 1]
    
    const response = await fetch(`${process.env.BACKEND_URL}/api/v1/chat/message`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${req.headers.authorization}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message: lastMessage.content,
        user_id: req.user.id, // From your auth middleware
      }),
    })

    const data = await response.json()
    
    if (data.success) {
      return res.status(200).json({
        id: Date.now().toString(),
        role: 'assistant',
        content: data.response,
      })
    } else {
      return res.status(500).json({ error: data.error })
    }
  } catch (error) {
    return res.status(500).json({ error: 'Internal server error' })
  }
}
```

## Example Queries

### Spending Analysis
- "Show me my spending for this month"
- "What did I spend most on last week?"
- "How much did I spend this year?"
- "Show me my spending breakdown for last month"

### Category Tracking
- "How much have I spent on groceries this month?"
- "Show me my restaurant spending for last week"
- "What's my entertainment spending this year?"
- "How much did I spend on transportation?"

### Trend Analysis
- "Show me my spending trends over the last 3 months"
- "Which categories are increasing?"
- "How has my spending changed this year?"
- "What are my top spending categories?"

### Account Balances
- "What's my current balance across all accounts?"
- "Show me my savings account balance"
- "What's the balance in my checking account?"
- "How much money do I have in total?"

### Transaction Management

#### Quick Expense Entry
- "Add $25 for lunch at McDonald's"
- "Record $150 gas expense"
- "Add $50 for groceries"
- "Record $30 for coffee"
- "Add $200 for dinner at restaurant"

#### Income Recording
- "Add $2000 salary deposit"
- "Record $500 freelance payment"
- "Add $1000 bonus"
- "Record $200 refund"
- "Add $1500 commission payment"

#### Transfer Tracking
- "Transfer $500 from checking to savings"
- "Move $100 to crypto wallet"
- "Transfer $200 from savings to checking"
- "Move $50 to emergency fund"
- "Transfer $1000 from checking to investment account"

## Supported Time Periods

- `this month` - Current month from the 1st to today
- `last month` - Previous month
- `this week` - Current week (Monday to today)
- `last week` - Previous week (Monday to Sunday)
- `this year` - Current year from January 1st to today

## Transaction Parsing Features

### Amount Recognition
- Currency symbols: `$25`, `$25.50`
- Written amounts: `25 dollars`, `25 bucks`
- Currency codes: `25 USD`

### Category Detection
- **Food**: lunch, dinner, breakfast, restaurant, coffee, food, groceries
- **Transport**: gas, fuel, uber, taxi, bus, train, transport
- **Entertainment**: movie, bar, drinks, entertainment
- **Shopping**: clothes, shoes, electronics, shopping

### Account Detection
- Automatically detects account names mentioned in the message
- Falls back to default accounts if none specified
- For transfers, intelligently determines "from" and "to" accounts

### Date Handling
- Defaults to today's date
- Recognizes "yesterday" keyword
- Can be extended to support more date formats

## Technical Architecture

### Components

1. **FinancialAnalytics** (`app/ai/financial_analytics.py`)
   - Core analytics engine
   - Handles data aggregation and calculations
   - Provides structured analysis results

2. **TransactionParser** (`app/ai/transaction_parser.py`)
   - Natural language transaction parsing
   - Extracts amounts, descriptions, categories, accounts
   - Creates database transactions

3. **FinancialAgent** (`app/ai/financial_agent.py`)
   - PydanticAI agent with financial tools
   - Natural language processing
   - Tool orchestration for analytics and transactions

4. **Chat Endpoints** (`app/api/api_v1/endpoints/chat.py`)
   - REST API endpoints
   - Streaming support
   - Transaction creation endpoint
   - Error handling and logging

### Data Models

- `SpendingAnalysis` - Comprehensive spending analysis
- `CategoryAnalysis` - Category-specific analysis
- `TrendAnalysis` - Time-based trend analysis
- `AccountBalance` - Account balance information
- `ParsedTransaction` - Transaction details from natural language

## Setup and Installation

1. Install dependencies:
```bash
cd backend
uv sync
```

2. Set up environment variables:
```bash
OPENAI_API_KEY=your_openai_api_key
```

3. Run the backend:
```bash
uvicorn app.main:app --reload
```

## Error Handling

The chatbot includes comprehensive error handling:

- Database connection errors
- Invalid user queries
- Missing data scenarios
- Transaction parsing errors
- API rate limiting
- Authentication failures

All errors are logged and return user-friendly messages.

## Performance Considerations

- Database queries are optimized with proper indexing
- Caching can be added for frequently requested data
- Streaming responses reduce perceived latency
- Async/await patterns for better concurrency
- Transaction parsing is optimized for common patterns

## Security

- JWT-based authentication required
- User data isolation
- Input validation and sanitization
- Rate limiting support
- Secure API key handling
- Transaction validation and verification

## Monitoring and Logging

- Request/response logging
- Error tracking
- Performance metrics
- User activity monitoring
- Transaction creation tracking

Logs are stored in `chat_requests.log` for debugging and monitoring.

## Future Enhancements

- **Smart Suggestions**: Suggest categories and accounts based on transaction history
- **Recurring Transactions**: Set up automatic recurring transactions
- **Budget Alerts**: Get notified when approaching budget limits
- **Receipt Scanning**: OCR integration for receipt processing
- **Voice Input**: Voice-to-text for hands-free transaction entry
- **Multi-language Support**: Support for multiple languages
- **Advanced Date Parsing**: Support for relative dates like "last Friday"
- **Transaction Templates**: Save and reuse common transaction patterns 