import os
import time
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from plugins.packages.PDCM.vol_reader_fun import price_reader
from plugins.packages.SSPM.model import train_model, predict_sent, loss, loss_perc, kalman


class SentimentPredictor:
    def __init__(self, config):
        # Configuration
        self.constituents_path = config.get("constituents_path")
        self.fig_loc = config.get("fig_loc")
        self.input_path = config.get("input_path")
        self.start_date = config.get("start_date")
        self.end_date = config.get("end_date")
        self.window = [self.start_date, self.end_date]



        # Create output directory if it doesn't exist
        if not os.path.exists(self.fig_loc):
            os.makedirs(self.fig_loc)

        # Initialize variables
        self.df_all = None
        self.mod_sent_ret = None
        self.mod_sent_vol = None

    def load_data(self):
        # Load main dataset
        dataset = pq.ParquetDataset(self.input_path)
        table = dataset.read()
        self.df_all = table.to_pandas()
        self.df_all = self.df_all.reset_index()
        self.df_all = self.df_all.sort_values(by=["Date", "_cik"]).reset_index(drop=True)
        self.df_all = self.df_all.drop(columns=["level_0"], errors="ignore")
        self.df_all = self.df_all.set_index("Date")
        self.df_all.index = pd.to_datetime(self.df_all.index, utc=True)
        self.df_all.index = self.df_all.index.to_series().dt.strftime("%Y-%m-%d")
        self.df_all["_ret"] = self.df_all["_ret"] / 100
        self.df_all = self.df_all.fillna(0.0)


    def train_model(self):
        # Train model for '_ret' and '_vol'
        df_trn = self.df_all.sort_index()[: f"{self.window[1]}"]
        kappa = self.adj_kappa(0.9)
        alpha_high = 100
        llambda = 0.1

        for dep in ["_ret", "_vol"]:
            mod = train_model(df_trn, dep, kappa, alpha_high, pprint=False)
            preds = predict_sent(mod, df_trn.drop(columns=["_cik", "_vol", "_ret"]), llambda)

            if dep == "_ret":
                self.mod_sent_ret = pd.Series(preds, index=df_trn.index)
            else:
                self.mod_sent_vol = pd.Series(preds, index=df_trn.index)
                
    def kalman_filter(self):
        mod_avg_ret = self.mod_sent_ret.groupby(self.mod_sent_ret.index).mean()
        mod_avg_vol = self.mod_sent_vol.groupby(self.mod_sent_vol.index).mean()
        mod_kal_ret = kalman(mod_avg_ret, smooth=True)
        mod_kal_vol = kalman(mod_avg_vol, smooth=True)
        return mod_kal_ret, mod_kal_vol

    def adj_kappa(self, k, k_min=0.85):
        return 1 - (1 - k) / (1 - k_min)

    def save_results(self):
        mod_sent_ret, mod_sent_vol = self.kalman_filter
        # Save results to CSV
        mod_sent_ret.to_csv(f"{self.fig_loc}/mod_sent_ret.csv")
        mod_sent_vol.to_csv(f"{self.fig_loc}/mod_sent_vol.csv")

    def run(self):
        # Run the entire workflow
        self.load_data()
        self.train_model()
        self.save_results()




if __name__ == "__main__":
    
    # Example configuration
    config = {
        "constituents_path": os.path.join(os.getenv("AIRFLOW_HOME", "/opt/airflow"), "data/constituents/firms/nvidia_constituents_final.csv"),
        "fig_loc": os.path.join(os.getenv("AIRFLOW_HOME", "/opt/airflow"), "data/SP500/sec/outcome/figures"),
        "input_path": os.path.join(os.getenv("AIRFLOW_HOME", "/opt/airflow"), "data/SP500/sec/processed/dtm_0001045810.parquet"),
        "start_date": "2006-01-01",
        "end_date": "2024-12-31",


}
    predictor = SentimentPredictor(config)
    predictor.run()