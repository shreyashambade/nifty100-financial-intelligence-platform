from celery import shared_task
import subprocess
import os

@shared_task
def run_etl_pipeline():
    """
    PRD Schedule: Daily at 1:00 AM
    Runs ETL scripts 2 (Clean) and 3 (Load).
    """
    # Assuming your etl folder is at the root of the project
    etl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'etl')
    
    # Execute Script 2
    subprocess.run(["python", "02_clean_and_transform.py"], cwd=etl_dir)
    # Execute Script 3
    subprocess.run(["python", "03_load_to_warehouse.py"], cwd=etl_dir)
    
    return "ETL Pipeline Executed Successfully"

@shared_task
def score_all_companies():
    """
    PRD Schedule: Daily at 2:00 AM
    Recalculates health scores for all 100 companies.
    """
    # Trigger your Notebook 2 / scoring script logic here
    return "Company scores updated"
    
@shared_task
def detect_anomalies():
    """
    PRD Schedule: Weekly on Sunday
    Z-score anomaly detection across all fact tables.
    """
    # Trigger your Notebook 3 logic here
    return "Anomalies detected and logged"from celery import shared_task
import subprocess
import os

@shared_task
def run_etl_pipeline():
    """
    PRD Schedule: Daily at 1:00 AM
    Runs ETL scripts 2 (Clean) and 3 (Load).
    """
    # Assuming your etl folder is at the root of the project
    etl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'etl')
    
    # Execute Script 2
    subprocess.run(["python", "02_clean_and_transform.py"], cwd=etl_dir)
    # Execute Script 3
    subprocess.run(["python", "03_load_to_warehouse.py"], cwd=etl_dir)
    
    return "ETL Pipeline Executed Successfully"

@shared_task
def score_all_companies():
    """
    PRD Schedule: Daily at 2:00 AM
    Recalculates health scores for all 100 companies.
    """
    # Trigger your Notebook 2 / scoring script logic here
    return "Company scores updated"
    
@shared_task
def detect_anomalies():
    """
    PRD Schedule: Weekly on Sunday
    Z-score anomaly detection across all fact tables.
    """
    # Trigger your Notebook 3 logic here
    return "Anomalies detected and logged"