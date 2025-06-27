from datetime import datetime

from pydantic import BaseModel

from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai import Agent, DocumentUrl, ImageUrl
from app.core.config import settings
from app import crud, models, schemas



class User(BaseModel):
    name: str
    age: int


agent = Agent(OpenAIModel('gpt-4o-mini', provider=OpenAIProvider(api_key=settings.OPENAI_API_KEY)))

@agent.tool_plain
def get_current_time() -> datetime:
    return datetime.now()


@agent.tool_plain
def get_user() -> User:
    return User(name='John', age=30)


@agent.tool_plain
def get_company_logo() -> ImageUrl:
    return ImageUrl(url='https://iili.io/3Hs4FMg.png')


@agent.tool_plain
def get_document() -> DocumentUrl:
    return DocumentUrl(url='https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf')
