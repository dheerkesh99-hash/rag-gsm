# GSM Support & Wiki Studio Installation Guide

Welcome! This guide provides comprehensive, step-by-step instructions on how to set up and run the GSM Support Demo and Wiki Studio applications from the provided zip files.

## Prerequisites
Before you begin, ensure you have the following installed on your machine:
- **Python 3.9+** ([Download here](https://www.python.org/downloads/))
- **Ollama** (Required only if you plan to run local models: [Download here](https://ollama.com/download))
- **OpenAI API Key** (Required to use GPT-4o models)

---

## Step 1: Extract the Files
1. Download `gsm_support_demo.zip` (for the Support Chat UI) or `wiki_studio.zip` (for the backend studio dashboard).
2. Extract the contents of the zip file into a dedicated folder on your computer.

---

## Step 2: Environment Setup
It is highly recommended to run these applications inside an isolated Python virtual environment.

1. Open your Terminal (Mac/Linux) or Command Prompt/PowerShell (Windows).
2. Navigate to the folder where you extracted the zip file:
   ```bash
   cd path/to/extracted/folder
   ```
3. Create a virtual environment:
   ```bash
   python -m venv venv
   ```
4. Activate the virtual environment:
   - **Mac/Linux:** `source venv/bin/activate`
   - **Windows:** `venv\Scripts\activate`
5. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

---

## Step 3: API Key Configuration
The application supports both cloud and local models. To enable the OpenAI models (GPT-4o), you must provide your API key.

1. Inside the extracted folder, create a new text file named exactly `.env`
2. Open the `.env` file and add the following line:
   ```text
   OPENAI_API_KEY="sk-your-actual-api-key-here"
   ```
3. Save the file.

---

## Step 4: Local Model Setup (Ollama)
If you wish to test the local models (like Gemma and Qwen) via the "Simultaneous Models" view, you must download them using Ollama.

1. Make sure the Ollama application is open and running in the background.
2. In your terminal, run the following commands to pull the models:
   ```bash
   ollama pull qwen3.5:9b
   ollama pull gemma4:e4b
   ```
*(Note: If you only plan to use OpenAI models, you can skip this step.)*

---

## Step 5: Running the Applications

### Running the GSM Support Demo (Chainlit UI)
To launch the primary support chat application where you can compare models side-by-side:
1. Ensure your virtual environment is activated.
2. Run the application using Chainlit:
   ```bash
   chainlit run app_v3.py -w
   ```
3. Your default web browser will automatically open to `http://localhost:8000` with the chat interface ready to use.

### Running Wiki Studio (FastAPI Backend)
To launch the Wiki Studio data management and ingestion backend:
1. Ensure your virtual environment is activated.
2. Run the server script:
   ```bash
   python server.py
   ```
3. Open your web browser and navigate to `http://localhost:8080`.

---

## Troubleshooting
- **Missing OPENAI_API_KEY Error:** Double-check that your `.env` file is in the root of the extracted folder and the key is properly enclosed in quotes.
- **Connection Error for Local Models:** Ensure that the Ollama app is actively running on your machine before submitting a query to local models.
