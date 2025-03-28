
import pendulum
from airflow import DAG
from airflow.decorators import task
from plugins.common.time_log_decorator import time_log

import time
import os
import pandas as pd
import datetime



with DAG(
    dag_id="sec_sentiment",
    schedule="0 0 1 1,4,7,10 *",
    start_date=pendulum.datetime(2025, 1, 1, tz="UTC"),
    catchup=False,

) as dag:
    
    ############################### Configurations ################################
    level = 'firm'
    type = ['10-K', '10-Q']
    start_date = '2025-01-01'
    end_date = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # Save File Paths
    base_path = os.getenv("AIRFLOW_HOME", "/opt/airflow")
    final_save_path = os.path.join(base_path, "data/SP500/sec")
    csv_file_path = os.path.join(base_path, "data/constituents/firms/nvidia_constituents_final.csv")
    columns = ["Name", "CIK", "Date", "Body" ]
    firms_df = pd.read_csv(csv_file_path)
    columns_to_drop = ['Security', 'GICS Sector', 'GICS Sub-Industry', 'Headquarters Location', 'Date added', 'Founded']
    firms_df = firms_df.drop(columns=columns_to_drop, errors='ignore')
    firms_df['CIK'] = firms_df['CIK'].apply(lambda x: str(x).zfill(10))
    
    seen = set()
    firms_ciks = [cik for cik in firms_df['CIK'].tolist() if not (cik in seen or seen.add(cik))] 
    
    # if level == 'firm':        
    data_raw_folder = os.path.join(base_path, "data/SP500/sec/html")
    extracted_folder = os.path.join(base_path, "data/SP500/sec/txt")

    error_html_csv_path = os.path.join(base_path, "data/error_html_log.csv")
    error_txt_csv_path = os.path.join(base_path, "data/error_txt_log.csv")

    if os.path.exists(error_html_csv_path):
        os.remove(error_html_csv_path)
    if os.path.exists(error_txt_csv_path):
        os.remove(error_txt_csv_path)         
    

    ###############################################################################
    @task(task_id='t1_test')
    def test(PATH):
    
        df = pd.read_csv(PATH, encoding = 'utf-8')
        cik = df['CIK'].drop_duplicates().tolist() 
        
        return cik
    
    @task(task_id='t2_download_executor')
    @time_log
    def download_executor(firm_list_path, type, start_date, end_date, **kwargs):
        from plugins.packages.FTRM.sec_crawler import download_fillings
        import os
        import pandas as pd
        
        try:
            df = pd.read_csv(firm_list_path, encoding = 'utf-8')
            cik = df['CIK'].drop_duplicates().tolist()
            ticker = df['Symbol'].tolist()
            cik_ticker = dict(zip(cik, ticker))
        except UnicodeDecodeError:
            df = pd.read_csv(firm_list_path, encoding = 'ISO-8859-1')
            cik = df['CIK'].drop_duplicates().tolist()
            ticker = df['Symbol'].tolist()
            cik_ticker = dict(zip(cik, ticker))
            
        # root_folder = '10k-html'
        for t in type:
            doc_type = t
            headers = {'User-Agent': 'University of Edinburgh s2101367@ed.ac.uk'}

            if not os.path.exists(data_raw_folder):
                os.makedirs(data_raw_folder)
            download_fillings(cik_ticker, data_raw_folder, doc_type,headers, start_date, end_date)
        
    
    @task(task_id='t3_txt_convertor')
    @time_log
    def txt_convertor(data_folder, save_folder, **kwargs):
        from plugins.packages.FTRM.sec_txt_extractor import process_fillings_for_cik
        import concurrent.futures
        import os
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            futures = []
            for cik in os.listdir(data_folder):
                future = executor.submit(process_fillings_for_cik, cik, data_folder, save_folder)
                futures.append(future)
                
                
            # Wait for all tasks to complete
            for future in futures:
                future.result()
            
            # All tasks are completed, shutdown the executor
            executor.shutdown()

        
    @task(task_id='t4_dtm_constructor')
    @time_log
    def dtm_constructor(**kwargs):
        import os
        from plugins.packages.PDCM.constructDTM import ConstructDTM
        from pyspark.sql import SparkSession
        
        os.environ['JAVA_HOME'] = '/usr/lib/jvm/java-11-openjdk-amd64'
        os.environ['SPARK_HOME'] = '/opt/spark-3.2.1-bin-hadoop3.2'
        # Initialize Spark session
        spark = (SparkSession.builder
            .appName("DataPipeline")
            .master("local[2]")
            # Memory allocations
            .config("spark.driver.memory", "2g")
            .config("spark.executor.memory", "2g")
            .config("spark.sql.shuffle.partitions", "4") 
            .getOrCreate()
        )
        pipeline = ConstructDTM(spark, extracted_folder, final_save_path, csv_file_path, columns, start_date, end_date)
        pipeline.file_aggregator()
        pipeline.process_filings_for_cik_spark(final_save_path, start_date, end_date, csv_file_path)
        constituents_metadata_path = os.path.join(base_path, "data/constituents/sp500_constituents.csv") # This is for getting the CIKs for the SP500, but only for the year 2006 - 2023
        pipeline.concatenate_parquet_files(final_save_path, csv_file_path, constituents_metadata_path)
        
    @task(task_id='t5_sent_predictor')
    @time_log
    def sent_predictor(**kwargs):
        from plugins.packages.SSPM.sent_predictor_firm import SentimentPredictor
        config = {
            "constituents_path": os.path.join(os.getenv("AIRFLOW_HOME", "/opt/airflow"), "data/constituents/firms/nvidia_constituents_final.csv"),
            "fig_loc": os.path.join(os.getenv("AIRFLOW_HOME", "/opt/airflow"), "data/SP500/sec/outcome/figures"),
            "input_path": os.path.join(os.getenv("AIRFLOW_HOME", "/opt/airflow"), "data/SP500/sec/processed/dtm_0001045810.parquet"),
            "start_date": "2006-01-01",
            "end_date": "2024-12-31",
            }
        predictor = SentimentPredictor(config)
        predictor.run()
    

    
    #FTRM
    t2_download_executor = download_executor(csv_file_path, type = type, start_date=start_date, end_date=end_date)
    t3_txt_convertor = txt_convertor(data_raw_folder, extracted_folder)
    #PDCM
    t4_dtm_constructor = dtm_constructor()

    #SSPM
    t5_sent_predictor = sent_predictor()

    
    t2_download_executor >> t3_txt_convertor >> t4_dtm_constructor >> t5_sent_predictor

        