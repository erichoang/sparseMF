import pandas as pd
import numpy as np
import graphlab
from fancyimpute import SoftImpute
from collections import defaultdict
import gc
from sparse_soft_impute import SoftImpute as SparseSoftImputeSVD
from sparse_als_soft_impute import SparseSoftImputeALS
from time import time
from sklearn.preprocessing.imputation import Imputer
from scipy.sparse import coo_matrix, csr_matrix 
from sklearn.metrics import mean_squared_error
import matplotlib.pyplot as plt
from sparse_biscale import SBiScale
from bench_gridsearch_pipeline import SIGrid, GLGrid 
from sparse_lnocv import MaskData

def load_movielens(path, leave_n_out=3, sample=500, seed=0):
    np.random.seed(seed)
    df = pd.read_csv(path).sample(sample, random_state=2)   
    n = len(df)
    row = np.array(df.userId)
    col = np.array(df.movieId)
    data = np.array(df.rating)
    mat = coo_matrix((data, (row, col)))
    masker = MaskData(mat, leave_n_out)
    train, test, test_data_points, test_results = masker.mask()
    return train, test, test_data_points, test_results 

def gALS_predict(train, test_data_points, max_iters=50, max_rank=8, sgd_step_size=0, regularization=1e-08, linear_regularization=1e-10):
    c = coo_matrix(train)
    sf = graphlab.SFrame({'row': c.row, 'col': c.col, 'data': c.data})
    sf_small = sf.dropna('data', how="all")
    sf_topredict = graphlab.SFrame({'row': test_data_points[0], 'col': test_data_points[1]})
    #sgd_step_size of zero means GL is tuning this automatically
    #num_factors and the regularization parameters are the default
    m1 = graphlab.factorization_recommender.create(sf_small, user_id='row', item_id='col', solver='als', max_iterations=max_iters, num_factors=max_rank, regularization=regularization, linear_regularization=linear_regularization, sgd_step_size=sgd_step_size, target='data', verbose=False)
    spred = m1.predict(sf_topredict)
    pred = spred.to_numpy()
    train_rmse = m1.evaluate_rmse(sf_small, target='data')['rmse_overall']
    return pred, train_rmse

def softALS_predict(train, test_data_points, max_rank=8, shrinkage_value=.02, max_iters=50, convergence_threshold=1e-05):
    train = csr_matrix(train)
    # I lowered the convergence threshold to see if that strengthened the model
    si = SparseSoftImputeALS(max_rank=max_rank, shrinkage_value=shrinkage_value, max_iters=max_iters, convergence_threshold=convergence_threshold, verbose=True)
    si.complete(train)
    pred = si.predict(test_data_points[0], test_data_points[1]) 
    training_rmse = si.eval()
    return pred, training_rmse

def fancy_biscale(train):
    train = csr_matrix(train)
    sb = SBiScale()
    train = sb.fit(train)
    rowscale = train.row_scale
    colscale = train.col_scale
    rowcenter = train.row_center
    colcenter = train.col_center
    return train.toarray(), rowscale, colscale, rowcenter, colcenter

def fancy_remove_biscale(y, rowscale, colscale, rowcenter, colcenter):
    result = np.empty(len(y))
    for i, (prediction, row_id, col_id) in enumerate(y):
        scaled = prediction * rowscale[row_id] * colscale[col_id]
        centered = scaled + rowcenter[row_id] + colcenter[col_id]
        result[i] = centered
    return result

def fancy_predict(train, test_data_points, max_rank=8, shrinkage_value=0.02, max_iters=50):
    train, rowscale, colscale, rowcenter, colcenter = fancy_biscale(train)
    train[train == 0] = np.nan
    si = SoftImpute(shrinkage_value=shrinkage_value, max_rank=max_rank, max_iters=max_iters, init_fill_method='zero', verbose=False)
    complete = si.complete(train)
    targets = zip(test_data_points[0], test_data_points[1])
    res = [] 
    for idx, (r, c) in enumerate(targets):
        res.append((complete[r,c], r, c))
    res = fancy_remove_biscale(res, rowscale, colscale, rowcenter, colcenter) 
    mask = np.isnan(train)
    train_rmse = np.sqrt(np.mean(np.abs(train[mask] - complete[mask])**2)) 
    return res, train_rmse
    
def iter_bench(path='movielens.csv', n_iter=1, leave_n_out=3, sample=1000):
    it = 0
    results = defaultdict(lambda: [])

    for idx in xrange(n_iter):
        train, test, test_data_points, test_results = load_movielens(path, seed=0, leave_n_out=leave_n_out, sample=sample)
        # confirmed level of sparsity is kept constant but datapoints selected change
        # be careful to have a large enough sample size that the same pairs aren't left out repeatedly

        # tuned parameters for SoftImpute on this training set. Graphlab does the equivalent in its actual algorithm
        #si_params = SIGrid(train)
        #gl_params = GLGrid(train)

        it += 1
        print('====================')
        print('LNOCV Iteration %03d of %03d' % (idx+1, n_iter))
        print('====================')
        inner_it = 0
        for i in xrange(0, 5):
            inner_it += 1
            if idx == 0:
                results['GraphLabALS'].append([])
                results['SoftImputeALS'].append([])
                results['FancyImpute'].append([])

            t2 = train.copy()
            gc.collect()
            print("benchmarking GraphLabALS: ")
            tstart = time()
            pred, train_rmse = gALS_predict(t2, test_data_points, max_rank=i+1, sgd_step_size=0.02, max_iters=10)
            #pred = gALS_predict(t2, test_data_points, max_rank=gl_params['num_factors'], sgd_step_size=gl_params['sgd_step_size'], max_iters=i+1)
            mse = mean_squared_error(test_results, pred)
            results['GraphLabALS'][inner_it-1].append((time() - tstart, mse, train_rmse))
            
            t4 = train.copy()
            gc.collect()
            print("benchmarking SoftImputeALS: ")
            tstart = time()
            pred, train_rmse = softALS_predict(t4, test_data_points, max_rank=i+1, shrinkage_value=9, max_iters=10)
            #pred = softALS_predict(t4, test_data_points, max_rank=si_params['max_rank'], shrinkage_value=si_params['shrinkage_value'], max_iters=i+1)
            mse = mean_squared_error(test_results, pred)
            results['SoftImputeALS'][inner_it-1].append((time() - tstart, mse, train_rmse))
           
            t3 = train.copy()
            gc.collect()
            print("benchmarking FancyImpute: ")
            tstart = time()
            pred, train_rmse = fancy_predict(t3, test_data_points, max_rank=i+1, shrinkage_value=9, max_iters=4)
            #pred = fancy_predict(t3, test_data_points, max_rank=si_params['max_rank'], shrinkage_value=si_params['shrinkage_value'], max_iters=i+1)
            mse = mean_squared_error(test_results, pred)
            results['FancyImpute'][inner_it-1].append((time() - tstart, mse, train_rmse))

    return results, train, test_data_points, test_results #, si_params#, gl_params

def compute_avgs(results, rmse=defaultdict(list), time=defaultdict(list), train_rmse=defaultdict(list)):
    rmse = rmse
    time = time 
    train_rmse = train_rmse
    for k, v in results.iteritems():
        for max_iter in v:
            avg_time = []
            avg_mse = []
            avg_train_rmse = []
            for trial in max_iter:
                avg_time.append(trial[0])
                avg_mse.append(trial[1])
                avg_train_rmse.append(trial[2])
            avg_time = np.array(avg_time)
            avg_rmse = np.sqrt(np.array(avg_mse))
            avg_time = np.mean(avg_time)
            avg_rmse = np.mean(avg_rmse)
            avg_train_rmse = np.mean(avg_train_rmse)
            rmse[k].append(avg_rmse)
            time[k].append(avg_time)
            train_rmse[k].append(avg_train_rmse)
    return rmse, time, train_rmse

def plot_RMSE_iters(results):
    ''' This plot shows how my algorithm compares to GraphLab in terms of RMSE '''
    f, (ax) = plt.subplots(1, 1)
    n_range = np.linspace(0, 50, 11)
    model_names = results[0].keys()
    model_range = range(len(model_names))
    for idx, model in enumerate(model_names): 
        ax.plot(n_range, results[0][model], label=model)
    ax.set_title("Iterations to Convergence")
    plt.ylabel('Root Mean Squared Error')
    plt.xlabel('Number of Iterations')
    plt.ylim(0, 0.5)
    plt.legend()
    plt.show()

if __name__ == '__main__':
   # results, train, test = compute_bench('movielens.csv', n_iter=3)
   # plot_results(results)
    #path = 'movielens.csv'

    #train, test, test_data_points, test_results = load_movielens(path, seed=0, leave_n_out=3, sample=10000)
    #si_params = SIGrid(train)
    results, train, test_data_points, test = iter_bench()
    #param_backup = (si_params, gl_params)
    #backup = results.copy()
    #avg = compute_avgs(results)
    #plot_over_samples(path)
    #train, test, testdatapoints, _ = load_movielens(path, sample=1000)
    #print gpredict(train, testdatapoints)
    #train = train.toarray()
    #bis = biscale(train)
    #print my_predict(train, testdatapoints)
    #bis_masked = bis * (train != 0)
    #cbis = csr_matrix(bis_masked)
    #print gpredict(train, testdatapoints)
    #print my_predict(train, testdatapoints)
    #print bis
