"""Developer-only tools. NOTHING in the bot service (bot/) or the ingest scripts (rag/) may import this package.

db.py reads the old South Jackson MySQL views. The evaluation scripts in evals/ and the check scripts in this package use it to compare the
bot's answers with the database ("ground truth"). The service itself gets all store data through the storefront API and holds
no database credentials; tests/test_no_database_in_service.py fails if that ever changes.
"""
