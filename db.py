import decouple
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DEBUG = decouple.config("DEBUG", default=False, cast=bool)

engine = create_async_engine(decouple.config("DATABASE_URL"), echo=DEBUG)

# expire_on_commit=False will prevent attributes from being expired
# after commit.
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
