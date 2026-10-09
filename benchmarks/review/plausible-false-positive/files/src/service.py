def fetch(url):
    return client.get(url).json()


def save(path, data):
    open(path, 'w').write(data)
