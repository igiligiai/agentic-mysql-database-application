import os

import uvicorn


def main() -> None:
    """Start the FastAPI backend.

    Run the Streamlit frontend separately:
        uv run streamlit run front/app.py
    """
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("api.app:app", host="0.0.0.0", port=port, reload=True)


if __name__ == "__main__":
    main()
