import redis

r = redis.from_url('redis://localhost:6379/0', decode_responses=True)
print('Ping:', r.ping())
r.setex('test_key', 10, 'hello')
print('Read:', r.get('test_key'))
print('Redis is working correctly')