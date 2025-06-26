# Financial Analytics Chatbot

A powerful financial analytics chatbot built with PydanticAI and FastAPI, designed to integrate seamlessly with Vercel AI SDK.

## Features

### 📊 Financial Analytics & Insights
- **Spending Analysis**: "Show me my spending for this month" / "What did I spend most on last week?"
- **Categories Tracking**: "How much have I spent on groceries this month?"
- **Trend Analysis**: "Show me my spending trends over the last 3 months" / "Which categories are increasing?"
- **Account Balances**: "What's my current balance across all accounts?" / "Show me my savings account balance"

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

### 3. Capabilities Endpoint
```http
GET /api/v1/chat/capabilities
```

Returns information about what the chatbot can do.

### 4. Test Endpoint
```http
POST /api/v1/chat/test
```

Tests the chat functionality with a sample message.

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
          placeholder="Ask about your finances..."
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
          placeholder="Ask about your finances..."
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

## Supported Time Periods

- `this month` - Current month from the 1st to today
- `last month` - Previous month
- `this week` - Current week (Monday to today)
- `last week` - Previous week (Monday to Sunday)
- `this year` - Current year from January 1st to today

## Technical Architecture

### Components

1. **FinancialAnalytics** (`app/ai/financial_analytics.py`)
   - Core analytics engine
   - Handles data aggregation and calculations
   - Provides structured analysis results

2. **FinancialAgent** (`app/ai/financial_agent.py`)
   - PydanticAI agent with financial tools
   - Natural language processing
   - Tool orchestration

3. **Chat Endpoints** (`app/api/api_v1/endpoints/chat.py`)
   - REST API endpoints
   - Streaming support
   - Error handling and logging

### Data Models

- `SpendingAnalysis` - Comprehensive spending analysis
- `CategoryAnalysis` - Category-specific analysis
- `TrendAnalysis` - Time-based trend analysis
- `AccountBalance` - Account balance information

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
- API rate limiting
- Authentication failures

All errors are logged and return user-friendly messages.

## Performance Considerations

- Database queries are optimized with proper indexing
- Caching can be added for frequently requested data
- Streaming responses reduce perceived latency
- Async/await patterns for better concurrency

## Security

- JWT-based authentication required
- User data isolation
- Input validation and sanitization
- Rate limiting support
- Secure API key handling

## Monitoring and Logging

- Request/response logging
- Error tracking
- Performance metrics
- User activity monitoring

Logs are stored in `chat_requests.log` for debugging and monitoring. 