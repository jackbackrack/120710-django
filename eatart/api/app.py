from fastapi import FastAPI

from eatart.api.routes.schemaorg import router as schemaorg_router


api = FastAPI(
    title='Eatart API',
    version='0.1.0',
    docs_url='/docs',
    redoc_url='/redoc',
    openapi_url='/openapi.json',
)


@api.get('/health')
def health_check():
    return {'status': 'ok'}


api.include_router(schemaorg_router)
