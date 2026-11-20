from urllib.parse import urlsplit


OPENAI_API_BASE_URL = "https://api.openai.com/v1"


def is_openai_endpoint(base_url: str) -> bool:
    try:
        url = urlsplit(base_url.strip())
    except ValueError:
        return False
    host = url.hostname or ""
    return url.scheme == "https" and (host == "api.openai.com" or host.endswith(".api.openai.com"))
