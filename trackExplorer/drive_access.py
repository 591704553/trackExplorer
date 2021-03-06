
import io
import os
import shutil
import tempfile

import pandas as pd

from google.oauth2 import service_account
from googleapiclient.errors import HttpError
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

try:
    from trackExplorer.fileio import read_history
except:
    from fileio import read_history

SCOPES = ['https://www.googleapis.com/auth/drive']

SERVICE_ACCOUNT_FILE = 'google-credentials.json'

credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)


service = build('drive', 'v3', credentials=credentials)


driveId = None
grid_list = None


def request_from_drive(q):
    """
    Basic google drive query that executes q and returns the first file/folder returned by the drive. If nothing is
    found it returns pd.NA

    @param q: query to run on google drive
    @return: the id of the first returned file/folder or pd.NA if nothing found
    """
    try:
        query_res = service.files().list(q=q, driveId=driveId, includeItemsFromAllDrives=True,
                                         supportsAllDrives=True, corpora='drive').execute()
        result = query_res['files'][0]['id']
        print(q, '\n--> Success')
    except (HttpError, IndexError) as e:
        print(q, '\n--> ', e)
        result = pd.NA

    return result


def get_drive_IDs(model):

    # get base folder Id
    q = "mimeType = 'application/vnd.google-apps.folder' and name = '{}'".format(model['folder_name'])
    base_folder_id = request_from_drive(q)

    # get model folder Id
    q = "mimeType = 'application/vnd.google-apps.folder' and name = '{}'".format(model['model_folder_name']) +\
        "and '{}' in parents".format(base_folder_id)
    model_folder_id = request_from_drive(q)

    # get summary file Id
    q = "'{}' in parents and name = '{}'".format(base_folder_id, model['summary_file'])
    summary_file_id = request_from_drive(q)

    return base_folder_id, model_folder_id, summary_file_id


def update_grid_list(force=False):
    
    global grid_list, driveId

    # get the drive Id, needs to be done always
    driveId = None
    all_drives = service.drives().list().execute()
    for drive in all_drives['drives']:
        if drive['name'] == 'MESA models':
            driveId = drive['id']

    if os.path.isfile('grid_list.csv') and not force:
        grid_list = pd.read_csv('grid_list.csv')
        if 'base_folder_id' in grid_list.columns and 'model_folder_id' in grid_list.columns and \
            'summary_file_id' in grid_list.columns:
            print ('Loaded grid list from file.')
            return
        else:
            print("Local grid list doesn't contain google drive ids")
    else:
        print("No local grid list found.")
    
    # get trackExplorer folder Id
    folders = service.files().list(q = "mimeType = 'application/vnd.google-apps.folder' and name = 'trackExplorer'", 
                                   driveId=driveId, includeItemsFromAllDrives=True, supportsAllDrives=True, corpora='drive').execute()
    folder_id = folders['files'][0]['id']
    
    # get the grid list fileId
    files = service.files().list(q = "'{}' in parents and name = 'Model_grid_info'".format(folder_id),
                                driveId=driveId, includeItemsFromAllDrives=True, supportsAllDrives=True, corpora='drive').execute() 
    file_id = files['files'][0]['id']

    request = service.files().export_media(fileId=file_id, mimeType='text/csv')
    fh = io.FileIO('grid_list.csv', 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
        
    grid_list = pd.read_csv('grid_list.csv')

    grid_list['base_folder_id'] = pd.NA
    grid_list['model_folder_id'] = pd.NA
    grid_list['summary_file_id'] = pd.NA

    # for each model in the grid_list, get the necessary file and folder IDs
    for i, model in grid_list.iterrows():
        base_id, model_id, summary_id = get_drive_IDs(model)
        grid_list.loc[i, 'base_folder_id'] = base_id
        grid_list.loc[i, 'model_folder_id'] = model_id
        grid_list.loc[i, 'summary_file_id'] = summary_id

    grid_list.to_csv('grid_list.csv', index=False)


def get_summary_file(gridname):
    global grid_list, driveId

    file_name = grid_list['summary_file'][grid_list['name'] == gridname].iloc[0]
    if os.path.isfile('temp/'+file_name):
        print('loading local grid: ', file_name)
        data = pd.read_csv('temp/'+file_name)

    else:
        file_id = grid_list['summary_file_id'][grid_list['name'] == gridname].iloc[0]

        request = service.files().get_media(fileId=file_id)
        with tempfile.NamedTemporaryFile() as temp:
            downloader = MediaIoBaseDownload(temp, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()

            data = pd.read_csv(temp.name)

    return data


def get_track(gridname, filename, folder_name=None, model_folder_name=None, save_filename=None):
    global grid_list, driveId

    if os.path.isfile('temp/'+filename):
        data = read_history('temp/'+filename)
        data = pd.DataFrame(data)

    else:
        folder_id = grid_list['model_folder_id'][grid_list['name'] == gridname].iloc[0]

        if folder_id is pd.NA and grid_list['model_folder_name'][grid_list['name'] == gridname].iloc[0] == "in_file":
            # in this case the folder_id is not the same for all models and needs to be obtained from the summary file
            print(folder_name, model_folder_name)
            q = "mimeType = 'application/vnd.google-apps.folder' and name = '{}'".format(folder_name)
            base_folder_id = request_from_drive(q)
            q = "mimeType = 'application/vnd.google-apps.folder' and name = '{}'".format(model_folder_name) + \
                "and '{}' in parents".format(base_folder_id)
            folder_id = request_from_drive(q)

        # get the fileId of the h5 file
        print(folder_id, filename)
        files = service.files().list(q="'{}' in parents and name = '{}'".format(folder_id, filename),
                                     driveId=driveId, includeItemsFromAllDrives=True, supportsAllDrives=True,
                                     corpora='drive').execute()

        if len(files['files']) == 0:
            print("File {} not found in folder {}".format(filename, folder_id))
            print("Searching in whole drive ...")

            files = service.files().list(q="name = '{}'".format(filename),
                                     driveId=driveId, includeItemsFromAllDrives=True, supportsAllDrives=True,
                                     corpora='drive').execute()

            if files['incompleteSearch']:
                print('The search was incomplete!')

            if len(files['files']) == 0:
                print("File {} not found in drive".format(filename))
                return None

        file_id = files['files'][0]['id']

        request = service.files().get_media(fileId=file_id)

        if save_filename is None:
            with tempfile.NamedTemporaryFile() as temp:
                downloader = MediaIoBaseDownload(temp, request)
                done = False
                while done is False:
                    status, done = downloader.next_chunk()

                data = read_history(temp.name)
                data = pd.DataFrame(data)
            return data
        else:
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()

            # The file has been downloaded into RAM, now save it in a file
            fh.seek(0)
            with open(save_filename, 'wb') as f:
                shutil.copyfileobj(fh, f, length=131072)

            print('get_track:', save_filename)

            return os.path.basename(save_filename)

    return data




update_grid_list()
# gridname = 'BPS shortP grid'
# filename = 'M2.642_M1.993_P7.11_Z0.02405.hdf5'.
# data = get_summary_file(gridname)
# data = get_track(gridname, filename)
# print(data)
