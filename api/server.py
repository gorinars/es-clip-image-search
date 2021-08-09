from sanic import Sanic
from sanic import exceptions
from sanic.log import logger
from sanic.response import json
from api.cors import add_cors_headers
import torch
import os
import clip
from elasticsearch import AsyncElasticsearch, TransportError

app = Sanic("image-search-api")
# Fill in CORS headers
app.register_middleware(add_cors_headers, "response")


device = "cuda" if torch.cuda.is_available() else "cpu"

model_name = os.environ['CLIP_MODEL_NAME']

logger.info('Loading clip model...')
model, preprocess = clip.load(model_name, device=device)
logger.info('done.')

index_name = os.environ.get('INDEX_NAME', 'images')

es = AsyncElasticsearch([os.environ.get('ES_URL')])

opensearch = AsyncElasticsearch([os.environ.get('OPENSEARCH_URL')])


def encode_query(query: str):
    with torch.no_grad():
        text_encoded = model.encode_text(clip.tokenize(query).to(device))
        text_encoded /= text_encoded.norm(dim=-1, keepdim=True)
        return text_encoded.tolist()[0]


@app.get("/")
async def search(request):
    db = request.args.get('db', 'elasticsearch')
    search_term = request.args.get('search', 'dogs playing in the snow')
    text_features = encode_query(search_term)
    try:
        if db == 'elasticsearch':
            resp = await es.search(
                index=index_name,
                body={
                    "query": {
                        "bool": {
                            "should": [
                                {
                                    "script_score": {
                                        "query": {"match_all": {}},
                                        "min_score": "1",
                                        "script": {
                                            "source":
                                            "cosineSimilarity(params.text_features, 'features')+1",
                                            "params": {"text_features": text_features},
                                        },
                                    },
                                },

                            ],
                        }
                    },
                    "_source": False
                },
                size=18,
                request_timeout=100
            )
            return json(resp)
        elif db == 'opensearch':
            resp = await opensearch.search(
                index=index_name,
                body={
                    "query": {
                        "knn": {
                            "features": {
                                "vector": text_features,
                                "k": 18
                            }
                        }
                    },
                    "_source": {
                        "excludes": ["features"]
                    },
                },
                size=18,
                request_timeout=100
            )
            return json(resp)
        else:
            raise exceptions.InvalidUsage(
                f'Invalid db={db}, valid values are "elasticsearch" and "opensearch"')
    except TransportError as e:
        logger.error(e.info)
        raise e
