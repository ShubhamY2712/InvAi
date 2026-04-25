from fastapi import FastAPI

# Initialize the InvAi application
app = FastAPI()

# Create your first API endpoint
@app.get("/")
def health_check():
    return {
        "system": "InvAi Backend",
        "status": "Online and Ready",
        "message": "Welcome to the future of inventory management."
    }