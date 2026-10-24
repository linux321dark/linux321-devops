from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"msg": "DevOps Journey Day1 - Python", "framework": "fastapi"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
