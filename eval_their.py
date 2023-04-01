from __future__ import print_function, unicode_literals
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import pip
import argparse
import json
import base64

def install(package):
    if hasattr(pip, 'main'):
        pip.main(['install', package])
    else:
        pip._internal.main(['install', package])

try:
    import open3d as o3d
except:
    install('open3d-python')
    import open3d as o3d

try:
    from scipy.linalg import orthogonal_procrustes
except:
    install('scipy')
    from scipy.linalg import orthogonal_procrustes


try:
    from utils.fh_utils import *
    from utils.eval_util import EvalUtil

except:
    from fh_utils import *
    from eval_util import EvalUtil


def verts2pcd(verts, color=None):
    pcd = o3d.PointCloud()
    pcd.points = o3d.Vector3dVector(verts)
    if color is not None:
        if color == 'r':
            pcd.paint_uniform_color([1, 0.0, 0])
        if color == 'g':
            pcd.paint_uniform_color([0, 1.0, 0])
        if color == 'b':
            pcd.paint_uniform_color([0, 0, 1.0])
    return pcd


def calculate_fscore(gt, pr, th=0.01):
    gt = verts2pcd(gt)
    pr = verts2pcd(pr)
    d1 = o3d.compute_point_cloud_to_point_cloud_distance(gt, pr) # closest dist for each gt point
    d2 = o3d.compute_point_cloud_to_point_cloud_distance(pr, gt) # closest dist for each pred point
    if len(d1) and len(d2):
        recall = float(sum(d < th for d in d2)) / float(len(d2))  # how many of our predicted points lie close to a gt point?
        precision = float(sum(d < th for d in d1)) / float(len(d1))  # how many of gt points are matched?

        if recall+precision > 0:
            fscore = 2 * recall * precision / (recall + precision)
        else:
            fscore = 0
    else:
        fscore = 0
        precision = 0
        recall = 0
    return fscore, precision, recall


def align_w_scale(mtx1, mtx2, return_trafo=False):
    """ Align the predicted entity in some optimality sense with the ground truth. """
    # center
    t1 = mtx1.mean(0)
    t2 = mtx2.mean(0)
    mtx1_t = mtx1 - t1
    mtx2_t = mtx2 - t2

    # scale
    s1 = np.linalg.norm(mtx1_t) + 1e-8
    mtx1_t /= s1
    s2 = np.linalg.norm(mtx2_t) + 1e-8
    mtx2_t /= s2

    # orth alignment
    R, s = orthogonal_procrustes(mtx1_t, mtx2_t)

    # apply trafos to the second matrix
    mtx2_t = np.dot(mtx2_t, R.T) * s
    mtx2_t = mtx2_t * s1 + t1
    if return_trafo:
        return R, s, s1, t1 - t2
    else:
        return mtx2_t


def align_by_trafo(mtx, trafo):
    t2 = mtx.mean(0)
    mtx_t = mtx - t2
    R, s, s1, t1 = trafo
    return np.dot(mtx_t, R.T) * s * s1 + t1 + t2


class curve:
    def __init__(self, x_data, y_data, x_label, y_label, text):
        self.x_data = x_data
        self.y_data = y_data
        self.x_label = x_label
        self.y_label = y_label
        self.text = text


def createHTML(outputDir, curve_list):
    curve_data_list = list()
    for idx, item in enumerate(curve_list):
        fig1 = plt.figure()
        ax = fig1.add_subplot(111)
        ax.plot(item.x_data, item.y_data)
        ax.set_xlabel(item.x_label)
        ax.set_ylabel(item.y_label)
        img_path = os.path.join(outputDir, f"curve_{idx}.png")
        plt.savefig(img_path, bbox_inches=0, dpi=300)

        # write image and create html embedding
        # data_uri1 = open(img_path, 'rb').read().encode('base64').replace('\n', '')
        with open(img_path, 'rb') as f:
            img_bytes = f.read()
            img_b64 = base64.b64encode(img_bytes)
            data_uri1 = img_b64.decode('utf-8')
            data_uri1 = data_uri1.replace('\n', '')
        img_tag1 = 'src="data:image/png;base64,{0}"'.format(data_uri1)
        curve_data_list.append((item.text, img_tag1))

        # os.remove(img_path)

    htmlString = '''<!DOCTYPE html>
    <html>
    <body>
    <h1>Detailed results:</h1>'''

    for i, (text, img_embed) in enumerate(curve_data_list):
        htmlString += '''
        <h2>%s</h2>
        <p>
        <img border="0" %s alt="FROC" width="576pt" height="432pt">
        </p>
        <p>Raw curve data:</p>
        
        <p>x_axis: <small>%s</small></p>
        <p>y_axis: <small>%s</small></p>
        
        ''' % (text, img_embed, curve_list[i].x_data, curve_list[i].y_data)

    htmlString += '''
    </body>
    </html>'''

    htmlfile = open(os.path.join(outputDir, "scores.html"), "w")
    htmlfile.write(htmlString)
    htmlfile.close()


def _search_pred_file(pred_path, pred_file_name):
    """ Tries to select the prediction file. Useful, in case people deviate from the canonical prediction file name. """
    pred_file = os.path.join(pred_path, pred_file_name)
    if os.path.exists(pred_file):
        # if the given prediction file exists we are happy
        return pred_file

    print('Predition file "%s" was NOT found' % pred_file_name)

    # search for a file to use
    print('Trying to locate the prediction file automatically ...')
    files = [os.path.join(pred_path, x) for x in os.listdir(pred_path) if x.endswith('.json')]
    if len(files) == 1:
        pred_file_name = files[0]
        print('Found file "%s"' % pred_file_name)
        return pred_file_name
    else:
        print('Found %d candidate files for evaluation' % len(files))
        raise Exception('Giving up, because its not clear which file to evaluate.')


def main(gt_path, pred_path, output_dir, pred_file_name=None, set_name=None, eval_verts=True):
    if pred_file_name is None:
        pred_file_name = 'pred.json'
    if set_name is None:
        set_name = 'evaluation'

    # load eval annotations
    xyz_list, verts_list = json_load(os.path.join(gt_path, '%s_xyz.json' % set_name)), json_load(os.path.join(gt_path, '%s_verts.json' % set_name))

    # load predicted values
    pred_file = _search_pred_file(pred_path, pred_file_name)
    print('Loading predictions from %s' % pred_file)
    with open(pred_file, 'r') as fi:
        pred = json.load(fi)

    assert len(pred) == 2, 'Expected format mismatch.'
    assert len(pred[0]) == len(xyz_list), 'Expected format mismatch.'
    if eval_verts:
        assert len(pred[1]) == len(xyz_list), 'Expected format mismatch.'

    # init eval utils
    eval_xyz, eval_xyz_aligned = EvalUtil(), EvalUtil()
    if eval_verts:
        eval_mesh_err, eval_mesh_err_aligned = EvalUtil(num_kp=778), EvalUtil(num_kp=778)
    f_score, f_score_aligned = list(), list()
    f_threshs = [0.005, 0.015]

    shape_is_mano = None

    try:
        from tqdm import tqdm
        rng = tqdm(range(db_size(set_name)))
    except:
        rng = range(db_size(set_name))

    # ----------------------------------------------------------
    import torch
    pose_align_all = []
    # pose_3d = (np.concatenate(pred[0], axis=0))
    # pose_3d_gt = (np.concatenate(xyz_list, axis=0))
    pose_3d = np.array(pred[0])
    pose_3d_gt = np.array(xyz_list)

    for idx in range(pose_3d.shape[0]):
        #align prediction
        pose_pred_aligned=align_w_scale(pose_3d_gt[idx], pose_3d[idx])
        pose_align_all.append(pose_pred_aligned)
    pose_align_all = torch.from_numpy(np.array(pose_align_all)).cuda()
    pose_3d_gt = torch.from_numpy(pose_3d_gt).cuda()

    pose_3d_loss = torch.linalg.norm((pose_align_all - pose_3d_gt), ord=2,dim=-1)
    pose_3d_loss = (np.concatenate(pose_3d_loss.detach().cpu().numpy(),axis=0)).mean()

    print("Evaluation pose 3d:", pose_3d_loss)
    print(f"Evaluation pose 3d: {pose_3d_loss * 100.0:.2f} cm")
    # ----------------------------------------------------------

    # iterate over the dataset once
    for idx in rng:
        if idx >= db_size(set_name):
            break

        xyz, verts = xyz_list[idx], verts_list[idx]
        xyz, verts = [np.array(x) for x in [xyz, verts]]

        xyz_pred = pred[0][idx]
        xyz_pred = np.array(xyz_pred)
        if eval_verts:
            verts_pred = pred[1][idx]
            verts_pred = np.array(verts_pred)

        # Not aligned errors
        eval_xyz.feed(
            xyz,
            np.ones_like(xyz[:, 0]),
            xyz_pred
        )

        if eval_verts:
            if shape_is_mano is None:
                if verts_pred.shape[0] == verts.shape[0]:
                    shape_is_mano = True
                else:
                    shape_is_mano = False

            if shape_is_mano:
                eval_mesh_err.feed(
                    verts,
                    np.ones_like(verts[:, 0]),
                    verts_pred
                )

        # align predictions
        xyz_pred_aligned = align_w_scale(xyz, xyz_pred)
        if eval_verts:
            if shape_is_mano:
                verts_pred_aligned = align_w_scale(verts, verts_pred)
            else:
                # use trafo estimated from keypoints
                trafo = align_w_scale(xyz, xyz_pred, return_trafo=True)
                verts_pred_aligned = align_by_trafo(verts_pred, trafo)

        # Aligned errors
        eval_xyz_aligned.feed(
            xyz,
            np.ones_like(xyz[:, 0]),
            xyz_pred_aligned
        )

        if eval_verts:
            if shape_is_mano:
                eval_mesh_err_aligned.feed(
                    verts,
                    np.ones_like(verts[:, 0]),
                    verts_pred_aligned
                )

            # F-scores
            l, la = list(), list()
            for t in f_threshs:
                # for each threshold calculate the f score and the f score of the aligned vertices
                f, _, _ = calculate_fscore(verts, verts_pred, t)
                l.append(f)
                f, _, _ = calculate_fscore(verts, verts_pred_aligned, t)
                la.append(f)
            f_score.append(l)
            f_score_aligned.append(la)

    # Calculate results
    xyz_mean3d, _, xyz_auc3d, pck_xyz, thresh_xyz = eval_xyz.get_measures(0.0, 0.05, 100)
    print('Evaluation 3D KP results:')
    print('auc=%.3f, mean_kp3d_avg=%.2f cm' % (xyz_auc3d, xyz_mean3d * 100.0))

    xyz_al_mean3d, _, xyz_al_auc3d, pck_xyz_al, thresh_xyz_al = eval_xyz_aligned.get_measures(0.0, 0.05, 100)
    print('Evaluation 3D KP ALIGNED results:')
    print('auc=%.3f, mean_kp3d_avg=%.2f cm\n' % (xyz_al_auc3d, xyz_al_mean3d * 100.0))

    if eval_verts:
        if shape_is_mano:
            mesh_mean3d, _, mesh_auc3d, pck_mesh, thresh_mesh = eval_mesh_err.get_measures(0.0, 0.05, 100)
            print('Evaluation 3D MESH results:')
            print('auc=%.3f, mean_kp3d_avg=%.2f cm' % (mesh_auc3d, mesh_mean3d * 100.0))

            mesh_al_mean3d, _, mesh_al_auc3d, pck_mesh_al, thresh_mesh_al = eval_mesh_err_aligned.get_measures(0.0, 0.05, 100)
            print('Evaluation 3D MESH ALIGNED results:')
            print('auc=%.3f, mean_kp3d_avg=%.2f cm\n' % (mesh_al_auc3d, mesh_al_mean3d * 100.0))
        else:
            mesh_mean3d, mesh_auc3d, mesh_al_mean3d, mesh_al_auc3d = -1.0, -1.0, -1.0, -1.0

            pck_mesh, thresh_mesh = np.array([-1.0, -1.0]), np.array([0.0, 1.0])
            pck_mesh_al, thresh_mesh_al = np.array([-1.0, -1.0]), np.array([0.0, 1.0])

        print('F-scores')
        f_out = list()
        f_score, f_score_aligned = np.array(f_score).T, np.array(f_score_aligned).T
        for f, fa, t in zip(f_score, f_score_aligned, f_threshs):
            print('F@%.1fmm = %.3f' % (t*1000, f.mean()), '\tF_aligned@%.1fmm = %.3f' % (t*1000, fa.mean()))
            f_out.append('f_score_%d: %f' % (round(t*1000), f.mean()))
            f_out.append('f_al_score_%d: %f' % (round(t*1000), fa.mean()))

    # Dump results
    score_path = os.path.join(output_dir, 'scores.txt')
    with open(score_path, 'w') as fo:
        xyz_mean3d *= 100
        xyz_al_mean3d *= 100
        fo.write('xyz_mean3d: %f\n' % xyz_mean3d)
        fo.write('xyz_auc3d: %f\n' % xyz_auc3d)
        fo.write('xyz_al_mean3d: %f\n' % xyz_al_mean3d)
        fo.write('xyz_al_auc3d: %f\n' % xyz_al_auc3d)

        if eval_verts:
            mesh_mean3d *= 100
            mesh_al_mean3d *= 100
            fo.write('mesh_mean3d: %f\n' % mesh_mean3d)
            fo.write('mesh_auc3d: %f\n' % mesh_auc3d)
            fo.write('mesh_al_mean3d: %f\n' % mesh_al_mean3d)
            fo.write('mesh_al_auc3d: %f\n' % mesh_al_auc3d)
            for t in f_out:
                fo.write('%s\n' % t)
    print('Scores written to: %s' % score_path)

    # scale to cm
    thresh_xyz *= 100.0
    thresh_xyz_al *= 100.0

    if eval_verts:
        thresh_mesh *= 100.0
        thresh_mesh_al *= 100.0

    if eval_verts:
        createHTML(
            output_dir,
            [
                curve(thresh_xyz, pck_xyz, 'Distance in cm', 'Percentage of correct keypoints', 'PCK curve for keypoint error'),
                curve(thresh_xyz_al, pck_xyz_al, 'Distance in cm', 'Percentage of correct keypoints', 'PCK curve for aligned keypoint error'),
                curve(thresh_mesh, pck_mesh, 'Distance in cm', 'Percentage of correct vertices', 'PCV curve for mesh error'),
                curve(thresh_mesh_al, pck_mesh_al, 'Distance in cm', 'Percentage of correct vertices', 'PCV curve for aligned mesh error')
            ]
        )
        pck_curve_data = {
            'xyz': [thresh_xyz.tolist(), pck_xyz.tolist()],
            'xyz_al': [thresh_xyz_al.tolist(), pck_xyz_al.tolist()],
            'mesh': [thresh_mesh.tolist(), pck_mesh.tolist()],
            'mesh_al': [thresh_mesh_al.tolist(), pck_mesh_al.tolist()],
        }
    else:
        createHTML(
            output_dir,
            [
                curve(thresh_xyz, pck_xyz, 'Distance in cm', 'Percentage of correct keypoints', 'PCK curve for keypoint error'),
                curve(thresh_xyz_al, pck_xyz_al, 'Distance in cm', 'Percentage of correct keypoints', 'PCK curve for aligned keypoint error'),
            ]
        )
        pck_curve_data = {
            'xyz': [thresh_xyz.tolist(), pck_xyz.tolist()],
            'xyz_al': [thresh_xyz_al.tolist(), pck_xyz_al.tolist()],
        }

    with open('pck_data.json', 'w') as fo:
        json.dump(pck_curve_data, fo)

    print('Evaluation complete.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Show some samples from the dataset.')
    parser.add_argument('--input_dir', type=str,
                        help='Path to where prediction the submited result and the ground truth is.')
    parser.add_argument('--output_dir', type=str, default='results',
                        help='Path to where the eval result should be.')
    parser.add_argument('--pred_file_name', type=str, default='pred.json',
                        help='Name of the eval file.')
    parser.add_argument('--gt_path', type=str, default='/storage_fast/jyzhu/HandRecon/freihand', 
                        help='Path to where the ground truth is.')
    parser.add_argument('--pred_path', type=str, default=None,
                        help='Path to where the submited result is.')
    parser.add_argument('--eval_verts', dest='eval_verts', action=argparse.BooleanOptionalAction,
                        help='Whether to eval verts. if not: --no-eval_verts')
    parser.add_argument('--set_name', type=str, default='evaluation', 
                        help='training set or evaluation set.')
    parser.set_defaults(eval_verts=True)
    args = parser.parse_args()

    # ** edited
    gt_path = args.gt_path if args.gt_path is not None else os.path.join(args.input_dir, 'ref')
    pred_path = args.pred_path if args.pred_path is not None else os.path.join(args.input_dir, 'res')
    print(f'Evaluation verts: {args.eval_verts}')
    print(f'Set name: {args.set_name}')

    # call eval
    main(
        gt_path,
        pred_path,
        args.output_dir,
        args.pred_file_name,
        set_name=args.set_name,
        eval_verts=args.eval_verts,
    )