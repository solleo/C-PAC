import os
import time
from time import strftime
import nipype.pipeline.engine as pe
import nipype.interfaces.fsl as fsl
import nipype.interfaces.io as nio
from nipype.interfaces.afni import preprocess
from   nipype.pipeline.utils import format_dot
import nipype.interfaces.ants as ants
import nipype.interfaces.c3 as c3
from nipype import config
from nipype import logging
from CPAC import network_centrality
from CPAC.network_centrality.utils import merge_lists
logger = logging.getLogger('workflow')
import pkg_resources as p
#import CPAC
from CPAC.anat_preproc.anat_preproc import create_anat_preproc
from CPAC.func_preproc.func_preproc import create_func_preproc
from CPAC.seg_preproc.seg_preproc import create_seg_preproc

from CPAC.registration import create_nonlinear_register, \
                              create_register_func_to_anat, \
                              create_bbregister_func_to_anat, \
                              create_wf_calculate_ants_warp, \
                              create_wf_apply_ants_warp, \
                              create_wf_c3d_fsl_to_itk, \
                              create_wf_collect_transforms
from CPAC.nuisance import create_nuisance, bandpass_voxels

from CPAC.median_angle import create_median_angle_correction
from CPAC.generate_motion_statistics import motion_power_statistics
from CPAC.generate_motion_statistics import fristons_twenty_four
from CPAC.scrubbing import create_scrubbing_preproc
from CPAC.timeseries import create_surface_registration, get_roi_timeseries, \
                            get_voxel_timeseries, get_vertices_timeseries, \
                            get_spatial_map_timeseries
from CPAC.network_centrality import create_resting_state_graphs
from CPAC.utils.datasource import *
from CPAC.utils import Configuration, create_all_qc
### no create_log_template here, move in CPAC/utils/utils.py
from CPAC.qc.qc import create_montage, create_montage_gm_wm_csf
from CPAC.qc.utils import register_pallete, make_edge, drop_percent_, \
                          gen_histogram, gen_plot_png, gen_motion_plt, \
                          gen_std_dev, gen_func_anat_xfm, gen_snr, \
                          generateQCPages, cal_snr_val
from CPAC.utils.utils import extract_one_d, set_gauss, \
                             get_scan_params, \
                             get_tr, extract_txt, create_log, \
                             create_log_template, extract_output_mean, \
                             create_output_mean_csv, get_zscore, \
                             get_fisher_zscore, dbg_file_lineno
from CPAC.vmhc.vmhc import create_vmhc
from CPAC.reho.reho import create_reho
from CPAC.alff.alff import create_alff
from CPAC.sca.sca import create_sca, create_temporal_reg
import zlib
import linecache
import csv
import pickle
import commands
#------------------------------------------------------------------------------

class strategy:
    """
    """
    def __init__(self):
        self.resource_pool = {}
        self.leaf_node = None
        self.leaf_out_file = None
        self.name = []

    def append_name(self, name):
        self.name.append(name)

    def get_name(self):
        return self.name

    def set_leaf_properties(self, node, out_file):
        self.leaf_node = node
        self.leaf_out_file = out_file

    def get_leaf_properties(self):
        return self.leaf_node, self.leaf_out_file

    def get_resource_pool(self):
        return self.resource_pool

    def get_node_from_resource_pool(self, resource_key,logger):
        try:
            if resource_key in self.resource_pool:
                return self.resource_pool[resource_key]
        except:
            logger.info('no node for output: ')
            logger.info(resource_key)
            raise

    def update_resource_pool(self, resources,logger):
        for key, value in resources.items():
##            if key in self.resource_pool:
##                logger.info('Warning: %s already exists in resource' \
##                        ' pool with value %s, \nreplacing it with this value %s ' %\
##                         (key, self.resource_pool[key],value))
            self.resource_pool[key] = value
            
            
def create_log_node(workflow,wflow, output, indx, log_dir,scan_id = None):
    """
    """
    if wflow: 
        log_wf = create_log(wf_name = 'log_%s' %wflow.name)
        log_wf.inputs.inputspec.workflow = wflow.name
        log_wf.inputs.inputspec.index = indx
        log_wf.inputs.inputspec.log_dir = log_dir
        workflow.connect(wflow, output, log_wf, 'inputspec.inputs')
    else:
        log_wf = create_log(wf_name = 'log_done_%s'%scan_id, scan_id= scan_id)
        log_wf.inputs.inputspec.workflow = 'DONE'
        log_wf.inputs.inputspec.index = indx
        log_wf.inputs.inputspec.log_dir = log_dir
        log_wf.inputs.inputspec.inputs = log_dir
        return log_wf

    
def populateLogger(message,logger):
    """
    """
    logger.info(message)
    
def logStandardError(sectionName, errLine, errNum,logger):
    """
    """    
    logger.info("\n\n" + 'ERROR: %s - %s' % (sectionName, errLine) + "\n\n" + \
                "Error name: cpac_pipeline_%s" % (errNum) + "\n\n")
        
def logConnectionError(workflow_name, numStrat, resourcePool, errNum,logger):
    """
    """       
    logger.info("\n\n" + 'ERROR: Invalid Connection: %s: %s, resource_pool: %s' \
                % (workflow_name, numStrat, resourcePool) +\
                 "\n\n" + "Error name: cpac_pipeline_%s" % (errNum) + \
                "\n\n" + "This is a pipeline creation error"+\
                " - the workflows have not started yet." + "\n\n")
        
def logStandardWarning(sectionName, warnLine,logger):
    """
    """    
    logger.info("\n\n" + 'WARNING: %s - %s' % (sectionName, warnLine) + "\n\n")
        
def getNodeList(strategy):
    """
    """    
    nodes = []
    for node in strategy.name:
        nodes.append(node[:-2])        
    return nodes
    
    
def inputFilepathsCheck(c):
    # this section checks all of the file paths provided in the pipeline
    # config yaml file and ensures the files exist and are accessible

    pipeline_config_map = c.return_config_elements()                
    wrong_filepath_list = []
    for config_item in pipeline_config_map:

        label = config_item[0]
        val = config_item[1]

        # ensures it is only checking file paths
        if isinstance(val, str) and '/' in val:
            if ('.txt' in val) or ('.nii' in val) or ('.nii.gz' in val) \
                or ('.mat' in val) or ('.cnf' in val) or ('.sch' in val):
                    
                if not os.path.isfile(val):
                    wrong_filepath_list.append((label, val))

    if len(wrong_filepath_list) > 0:
        print '\n\n'
        print 'Whoops! - Filepaths provided do not exist:\n'

        for file_tuple in wrong_filepath_list:
            print file_tuple[0], ' - ', file_tuple[1]

        print '\nPlease double-check your pipeline configuration file.\n\n'
        raise Exception
    
    
def workflowPreliminarySetup(subject_id,c,config,logging,log_dir,logger):
    
    wfname = 'resting_preproc_' + str(subject_id)
    workflow = pe.Workflow(name=wfname)
    workflow.base_dir = c.workingDirectory
    workflow.config['execution'] = {'hash_method': 'timestamp', \
                                    'crashdump_dir': os.path.abspath(c.crashLogDirectory)}
    config.update_config({'logging': {'log_directory': log_dir, 'log_to_file': True}})
    logging.update_logging(config)

    if c.reGenerateOutputs is True:
        cmd = "find %s -name \'*sink*\' -exec rm -rf {} \\;" % os.path.join(c.workingDirectory, wfname)
        commands.getoutput(cmd)
        cmd = "find %s -name \'*link*\' -exec rm -rf {} \\;" % os.path.join(c.workingDirectory, wfname)
        commands.getoutput(cmd)
        cmd = "find %s -name \'*log*\' -exec rm -rf {} \\;" % os.path.join(c.workingDirectory, wfname)
        commands.getoutput(cmd)
        
    return workflow
    
def runAnatomicalDataGathering(c,subject_id,sub_dict,workflow,\
                                workflow_bit_id,workflow_counter,\
                                strat_list,logger,log_dir):
    """
    """
    num_strat = 0
    strat_list = []
    for gather_anat in c.runAnatomicalDataGathering:
        strat_initial = strategy()
        if gather_anat == 1:
            flow = create_anat_datasource()
            flow.inputs.inputnode.subject = subject_id
            flow.inputs.inputnode.anat = sub_dict['anat']
            anat_flow = flow.clone('anat_gather_%d' % num_strat)
            strat_initial.set_leaf_properties(anat_flow, 'outputspec.anat')
        num_strat += 1
        strat_list.append(strat_initial) 
    return strat_list
    
def runAnatomicalPreprocessing(c,subject_id,sub_dict,workflow,\
                                workflow_bit_id,workflow_counter,\
                                strat_list,logger,log_dir):
    """
    """
    new_strat_list = []
    num_strat = 0
    if 1 in c.runAnatomicalPreprocessing:
        workflow_bit_id['anat_preproc'] = workflow_counter
        for strat in strat_list:
            anat_preproc = create_anat_preproc().clone('anat_preproc_%d' % num_strat)
            try: # connect the new node to the previous leaf                
                node, out_file = strat.get_leaf_properties()
                workflow.connect(node, out_file, anat_preproc, 'inputspec.anat')
            except:
                logConnectionError('Anatomical Preprocessing No valid Previous for strat', \
                                   num_strat, strat.get_resource_pool(), '0001',logger)
                num_strat += 1
                continue

            if 0 in c.runAnatomicalPreprocessing: # we are forking so create a new node                
                new_strat_list,strat=createNewStrategy(strat,new_strat_list) 

            strat.append_name(anat_preproc.name)
            strat.set_leaf_properties(anat_preproc, 'outputspec.brain')
            strat.update_resource_pool({'anatomical_brain':\
                                    (anat_preproc, 'outputspec.brain')},logger)
            strat.update_resource_pool({'anatomical_reorient':\
                                (anat_preproc, 'outputspec.reorient')},logger)            
            create_log_node(workflow,anat_preproc, 'outputspec.brain', \
                            num_strat,log_dir)
            num_strat += 1
    strat_list += new_strat_list
    return strat_list
    
def runRegistrationPreprocessing(c,subject_id,sub_dict,workflow,\
                                workflow_bit_id,workflow_counter,\
                                strat_list,logger,log_dir):
    """
    """
    new_strat_list=[]
    num_strat = 0    
    if 1 in c.runRegistrationPreprocessing:
        workflow_bit_id['anat_mni_register'] = workflow_counter
        for strat in strat_list:
            nodes = getNodeList(strat)
            if 'FSL' in c.regOption:
                num_strat = regOptionFSL(num_strat,strat,workflow,\
                                            logger,c,new_strat_list,log_dir)                    
            if ('ANTS' in c.regOption) and \
                    ('anat_mni_fnirt_register' not in nodes):
                num_strat = regOptionAnts(num_strat,strat,workflow,\
                                            logger,c,new_strat_list,log_dir) 
    strat_list += new_strat_list
    return strat_list 

def regOptionAnts(num_strat,strat,workflow,logger,c,new_strat_list,log_dir):
    """
    """
    ants_reg_anat_mni = create_wf_calculate_ants_warp('anat_mni_ants_register_%d' % num_strat)
    try:
        # calculating the transform with the skullstripped is reported to be better, but it requires very high
        # quality skullstripping. If skullstripping is imprecise registration with skull is preferred
        if (1 in c.regWithSkull):
            if c.already_skullstripped[0] == 3:
                err_msg = '\n\n[!] CPAC says: You selected to run anatomical registration with ' \
                'the skull, but you also selected to use already-skullstripped images as ' \
                'your inputs. This can be changed in your pipeline configuration editor.\n\n'
                logger.info(err_msg)
                raise Exception
            # get the reorient skull-on anatomical from resource pool
            node, out_file = strat.get_node_from_resource_pool('anatomical_reorient')
            # pass the anatomical to the workflow
            workflow.connect(node, out_file,ants_reg_anat_mni,'inputspec.anatomical_brain')
            # pass the reference file
            ants_reg_anat_mni.inputs.inputspec.reference_brain = c.template_skull_for_anat
        else:
            node, out_file = strat.get_leaf_properties()
            workflow.connect(node, out_file, ants_reg_anat_mni,'inputspec.anatomical_brain')
            ants_reg_anat_mni.inputs.inputspec.reference_brain = c.template_brain_only_for_anat  
        setAntsInputSpecs(ants_reg_anat_mni,c)    
    except:
        logConnectionError('Anatomical Registration (ANTS)', num_strat, \
                                    strat.get_resource_pool(), '0003',logger)
        raise

    if 0 in c.runRegistrationPreprocessing:
        new_strat_list,strat=createNewStrategy(strat,new_strat_list) 

    strat.append_name(ants_reg_anat_mni.name)
    strat.set_leaf_properties(ants_reg_anat_mni,'outputspec.normalized_output_brain')
    strat.update_resource_pool({\
        'ants_initial_xfm':(ants_reg_anat_mni, 'outputspec.ants_initial_xfm'),
        'ants_rigid_xfm':(ants_reg_anat_mni, 'outputspec.ants_rigid_xfm'),
        'ants_affine_xfm':(ants_reg_anat_mni, 'outputspec.ants_affine_xfm'),
        'anatomical_to_mni_nonlinear_xfm':(ants_reg_anat_mni, 'outputspec.warp_field'),
        'mni_to_anatomical_nonlinear_xfm':(ants_reg_anat_mni, 'outputspec.inverse_warp_field'),
        'anat_to_mni_ants_composite_xfm':(ants_reg_anat_mni, 'outputspec.composite_transform'),
        'mni_normalized_anatomical':(ants_reg_anat_mni, 'outputspec.normalized_output_brain')},\
        logger)
    create_log_node(workflow,ants_reg_anat_mni, \
                    'outputspec.normalized_output_brain', num_strat,log_dir)          
    num_strat += 1   
    return num_strat
            
              
def regOptionFSL(num_strat,strat,workflow,logger,c,new_strat_list,log_dir):
    """
    """
    if c.already_skullstripped[0] == 3:
        err_msg = '\n\n[!] CPAC says: FNIRT (for anatomical registration) will not work properly if you ' \
        'are providing inputs that have already been skull-stripped.\n\nEither switch to using ' \
        'ANTS for registration or provide input images that have not been already skull-stripped.\n\n'
        logger.info(err_msg)
        raise Exception
    
    fnirt_reg_anat_mni = create_nonlinear_register('anat_mni_fnirt_register_%d' % num_strat)
    try:
        node, out_file = strat.get_leaf_properties()
        workflow.connect(node, out_file,fnirt_reg_anat_mni, 'inputspec.input_brain')
        node, out_file = strat.get_node_from_resource_pool('anatomical_reorient',logger)
        workflow.connect(node, out_file,fnirt_reg_anat_mni, 'inputspec.input_skull')
        fnirt_reg_anat_mni = setFnirtInputSpecs(fnirt_reg_anat_mni,c)             
    except:
        logConnectionError('Anatomical Registration (FSL)', \
                           num_strat, strat.get_resource_pool(), '0002',logger)
        raise

    if (0 in c.runRegistrationPreprocessing) or ('ANTS' in c.regOption):
        new_strat_list,strat=createNewStrategy(strat,new_strat_list) 

    strat.append_name(fnirt_reg_anat_mni.name)
    strat.set_leaf_properties(fnirt_reg_anat_mni, 'outputspec.output_brain')
    strat.update_resource_pool({\
                'anatomical_to_mni_linear_xfm':(fnirt_reg_anat_mni, 'outputspec.linear_xfm'),\
                'anatomical_to_mni_nonlinear_xfm':(fnirt_reg_anat_mni, 'outputspec.nonlinear_xfm'),\
                'mni_to_anatomical_linear_xfm':(fnirt_reg_anat_mni, 'outputspec.invlinear_xfm'),\
                'mni_normalized_anatomical':(fnirt_reg_anat_mni, 'outputspec.output_brain')},\
                logger)
    create_log_node(workflow,fnirt_reg_anat_mni, 'outputspec.output_brain', num_strat,log_dir)         
    num_strat += 1 
    return num_strat        
                
                
                
def setFnirtInputSpecs(fnirt_reg_anat_mni,c):
    """
    """
    fnirt_reg_anat_mni.inputs.inputspec.reference_brain = c.template_brain_only_for_anat
    fnirt_reg_anat_mni.inputs.inputspec.reference_skull = c.template_skull_for_anat
    fnirt_reg_anat_mni.inputs.inputspec.fnirt_config = c.fnirtConfig  
    return fnirt_reg_anat_mni

  
def setAntsInputSpecs(ants_reg_anat_mni,c):
    """
    """
    ants_reg_anat_mni.inputs.inputspec.dimension = 3
    ants_reg_anat_mni.inputs.inputspec.use_histogram_matching = True
    ants_reg_anat_mni.inputs.inputspec.winsorize_lower_quantile = 0.01
    ants_reg_anat_mni.inputs.inputspec.winsorize_upper_quantile = 0.99
    ants_reg_anat_mni.inputs.inputspec.metric = ['MI','MI','CC']
    ants_reg_anat_mni.inputs.inputspec.metric_weight = [1,1,1]
    ants_reg_anat_mni.inputs.inputspec.radius_or_number_of_bins = [32,32,4]
    ants_reg_anat_mni.inputs.inputspec.sampling_strategy = ['Regular','Regular',None]
    ants_reg_anat_mni.inputs.inputspec.sampling_percentage = [0.25,0.25,None]
    ants_reg_anat_mni.inputs.inputspec.number_of_iterations = [[1000,500,250,100],[1000,500,250,100], [100,100,70,20]]
    ants_reg_anat_mni.inputs.inputspec.convergence_threshold = [1e-8,1e-8,1e-9]
    ants_reg_anat_mni.inputs.inputspec.convergence_window_size = [10,10,15]
    ants_reg_anat_mni.inputs.inputspec.transforms = ['Rigid','Affine','SyN']
    ants_reg_anat_mni.inputs.inputspec.transform_parameters = [[0.1],[0.1],[0.1,3,0]]
    ants_reg_anat_mni.inputs.inputspec.shrink_factors = [[8,4,2,1],[8,4,2,1],[6,4,2,1]]
    ants_reg_anat_mni.inputs.inputspec.smoothing_sigmas = [[3,2,1,0],[3,2,1,0],[3,2,1,0]] 
    return ants_reg_anat_mni
    
def runSegmentationPreprocessing(c,subject_id,sub_dict,workflow,\
                                workflow_bit_id,workflow_counter,\
                                strat_list,logger,log_dir):
    """
    """
    new_strat_list = []
    num_strat = 0
    if 1 in c.runSegmentationPreprocessing:
        workflow_bit_id['seg_preproc'] = workflow_counter
        for strat in strat_list:            
            nodes = getNodeList(strat)            
            if 'anat_mni_fnirt_register' in nodes:
                seg_preproc = create_seg_preproc(False, 'seg_preproc_%d' % num_strat)
            elif 'anat_mni_ants_register' in nodes:
                seg_preproc = create_seg_preproc(True, 'seg_preproc_%d' % num_strat)
            try:
                node, out_file = strat.get_node_from_resource_pool('anatomical_brain',logger)
                workflow.connect(node, out_file,
                                 seg_preproc, 'inputspec.brain')
                if 'anat_mni_fnirt_register' in nodes:
                    node, out_file = strat.get_node_from_resource_pool('mni_to_anatomical_linear_xfm',logger)
                    workflow.connect(node, out_file,seg_preproc, 'inputspec.standard2highres_mat')
                elif 'anat_mni_ants_register' in nodes:
                    node, out_file = strat.get_node_from_resource_pool('ants_affine_xfm',logger)
                    workflow.connect(node, out_file,seg_preproc, 'inputspec.standard2highres_mat')
                    node, out_file = strat.get_node_from_resource_pool('ants_rigid_xfm',logger)
                    workflow.connect(node, out_file,seg_preproc, 'inputspec.standard2highres_rig')
                seg_preproc= setSegInputSpecs(seg_preproc,c)
            except:
                logConnectionError('Segmentation Preprocessing', \
                                   num_strat, strat.get_resource_pool(), '0004',logger)
                raise

            if 0 in c.runSegmentationPreprocessing:
                new_strat_list,strat=createNewStrategy(strat,new_strat_list)               

            strat.append_name(seg_preproc.name)
            strat.update_resource_pool({'anatomical_gm_mask' : (seg_preproc, 'outputspec.gm_mask'),
                                        'anatomical_csf_mask': (seg_preproc, 'outputspec.csf_mask'),
                                        'anatomical_wm_mask' : (seg_preproc, 'outputspec.wm_mask'),
                                        'seg_probability_maps': (seg_preproc, 'outputspec.probability_maps'),
                                        'seg_mixeltype': (seg_preproc, 'outputspec.mixeltype'),
                                        'seg_partial_volume_map': (seg_preproc, 'outputspec.partial_volume_map'),
                                        'seg_partial_volume_files': (seg_preproc, 'outputspec.partial_volume_files')},logger)

            create_log_node(workflow,seg_preproc, \
                        'outputspec.partial_volume_map', num_strat,log_dir)
            num_strat += 1
    strat_list += new_strat_list 
    return strat_list   

def createNewStrategy(strat,new_strat_list):
    """
    """
    tmp = strategy()
    tmp.resource_pool = dict(strat.resource_pool)
    tmp.leaf_node = (strat.leaf_node)
    tmp.leaf_out_file = str(strat.leaf_out_file)
    tmp.name = list(strat.name)
    strat = tmp
    new_strat_list.append(strat)
    return new_strat_list,strat
    
def setSegInputSpecs(seg_preproc,c):
    """
    """
    seg_preproc.inputs.inputspec.PRIOR_CSF = c.PRIORS_CSF
    seg_preproc.inputs.inputspec.PRIOR_GRAY = c.PRIORS_GRAY
    seg_preproc.inputs.inputspec.PRIOR_WHITE = c.PRIORS_WHITE

    seg_preproc.inputs.csf_threshold.csf_threshold = \
                            c.cerebralSpinalFluidThreshold
    seg_preproc.inputs.wm_threshold.wm_threshold = \
                            c.whiteMatterThreshold
    seg_preproc.inputs.gm_threshold.gm_threshold = \
                            c.grayMatterThreshold
    seg_preproc.get_node('csf_threshold').iterables = ('csf_threshold',
                            c.cerebralSpinalFluidThreshold)
    seg_preproc.get_node('wm_threshold').iterables = ('wm_threshold',
                            c.whiteMatterThreshold)
    seg_preproc.get_node('gm_threshold').iterables = ('gm_threshold',
                            c.grayMatterThreshold)
    return seg_preproc
    
def runFunctionalDataGathering(c,subject_id,sub_dict,workflow,\
                                workflow_bit_id,workflow_counter,\
                                strat_list,logger,log_dir):
    """
    """
    new_strat_list = []
    num_strat = 0
    if 1 in c.runFunctionalDataGathering:
        for strat in strat_list:
            funcFlow = create_func_datasource(sub_dict['rest'], 'func_gather_%d' % num_strat)
            funcFlow.inputs.inputnode.subject = subject_id
            if 0 in c.runFunctionalDataGathering:
                new_strat_list,strat=createNewStrategy(strat,new_strat_list)
            strat.set_leaf_properties(funcFlow, 'outputspec.rest')
            num_strat += 1

    strat_list += new_strat_list
    return strat_list,funcFlow

def ants_apply_warps_func_mni(input_node, input_outfile, \
                        reference, interp, input_image_type, \
                        func_name,num_strat,workflow,log_dir,strat):
    """
    """
    # apply ants warps
    apply_ants_warp_func_mni = create_wf_apply_ants_warp(0, \
            name='apply_ants_warp_%s_%d' % (func_name, num_strat))
    apply_ants_warp_func_mni.inputs.inputspec.reference_image = reference
    apply_ants_warp_func_mni.inputs.inputspec.dimension = 3
    apply_ants_warp_func_mni.inputs.inputspec.interpolation = interp

    # input_image_type: (0 or 1 or 2 or 3)
    # Option specifying the input image type of scalar: (default), vector, tensor, or time series.
    apply_ants_warp_func_mni.inputs.inputspec.input_image_type = input_image_type

    try:
        # this <node, out_file> pulls in directly because
        # it pulls in the leaf in some instances
        workflow.connect(input_node, input_outfile,apply_ants_warp_func_mni, 'inputspec.input_image')
        node, out_file = strat.get_node_from_resource_pool('itk_collected_warps_%s' % func_name,logger)
        workflow.connect(node, out_file,apply_ants_warp_func_mni,'inputspec.transforms')
    except:
        logConnectionError('Functional Timeseries Registration to MNI space (ANTS)',\
                num_strat, strat.get_resource_pool(), '0016',logger)
        raise

    strat.update_resource_pool({func_name: (apply_ants_warp_func_mni, \
            'outputspec.output_image')},logger)

    strat.append_name(apply_ants_warp_func_mni.name)
    create_log_node(workflow,apply_ants_warp_func_mni, \
            'outputspec.output_image', num_strat,log_dir)

def fsl_to_itk_conversion(source_file, reference, func_name,num_strat,workflow,log_dir,strat):
    """
    """
    # converts FSL-format .mat affine xfm into ANTS-format
    # .txt; .mat affine comes from Func->Anat registration
    fsl_to_itk_func_mni = create_wf_c3d_fsl_to_itk(0, name=\
            'fsl_to_itk_%s_%d' % (func_name,num_strat))

    try:
        # convert the .mat from linear Func->Anat to ANTS format
        node, out_file = strat.get_node_from_resource_pool(\
                'functional_to_anat_linear_xfm',logger)
        workflow.connect(node, out_file, fsl_to_itk_func_mni,
                'inputspec.affine_file')

        node, out_file = strat.get_node_from_resource_pool(\
                reference,logger)
        workflow.connect(node, out_file, fsl_to_itk_func_mni,
                'inputspec.reference_file')

        node, out_file = strat.get_node_from_resource_pool(\
                source_file,logger)
        workflow.connect(node, out_file, fsl_to_itk_func_mni,
                'inputspec.source_file')
    except:
        logConnectionError('Functional Timeseries ' \
            'Registration to MNI space (ANTS)', num_strat, \
            strat.get_resource_pool(), '0016',logger)
        raise

    strat.update_resource_pool({'itk_func_anat_affine_%s' % \
            (func_name): (fsl_to_itk_func_mni,'outputspec.itk_transform')},logger)

    strat.append_name(fsl_to_itk_func_mni.name)
    create_log_node(workflow,fsl_to_itk_func_mni,'outputspec.itk_transform', num_strat,log_dir)
            
            
def collect_transforms_func_mni(func_name,num_strat,workflow,log_dir,strat):
    """
    collects series of warps to be applied
    """
    collect_transforms_func_mni = create_wf_collect_transforms(0, \
            name='collect_transforms_%s_%d' % (func_name, num_strat))
    try:
        # Field file from anatomical nonlinear registration
        node, out_file = strat.get_node_from_resource_pool(\
                'anatomical_to_mni_nonlinear_xfm',logger)
        workflow.connect(node, out_file,collect_transforms_func_mni,'inputspec.warp_file')

        # initial transformation from anatomical registration
        node, out_file = strat.get_node_from_resource_pool('ants_initial_xfm',logger)
        workflow.connect(node, out_file,collect_transforms_func_mni,'inputspec.linear_initial')

        # affine transformation from anatomical registration
        node, out_file = strat.get_node_from_resource_pool('ants_affine_xfm',logger)
        workflow.connect(node, out_file,collect_transforms_func_mni,'inputspec.linear_affine')

        # rigid transformation from anatomical registration
        node, out_file = strat.get_node_from_resource_pool('ants_rigid_xfm',logger)
        workflow.connect(node, out_file,collect_transforms_func_mni,'inputspec.linear_rigid')

        # Premat from Func->Anat linear reg and bbreg
        # (if bbreg is enabled)
        node, out_file = strat.get_node_from_resource_pool('itk_func_anat_affine_%s' % func_name,logger)
        workflow.connect(node, out_file,collect_transforms_func_mni,'inputspec.fsl_to_itk_affine')
    except:
        logConnectionError('Functional Timeseries ' \
            'Registration to MNI space (ANTS)', num_strat, \
            strat.get_resource_pool(), '0016',logger)
        raise
    
    strat.update_resource_pool({'itk_collected_warps_%s' % \
            (func_name): (collect_transforms_func_mni, \
            'outputspec.transformation_series')},logger)

    strat.append_name(collect_transforms_func_mni.name)
    create_log_node(workflow,collect_transforms_func_mni, \
            'outputspec.transformation_series', num_strat,log_dir)

def z_score_standardize(workflow,output_name,output_resource,strat,num_strat,logger,map_node=0):
    """
    """
    z_score_std = get_zscore(output_resource, 'z_score_std_%s_%d' % (output_name, num_strat))
    try:
        node, out_file = strat.get_node_from_resource_pool(output_resource,logger)
        workflow.connect(node, out_file, z_score_std, 'inputspec.input_file')

        # needs the template-space functional mask because we are z-score
        # standardizing outputs that have already been warped to template
        node, out_file = strat.get_node_from_resource_pool('functional_brain_mask_to_standard',logger)
        workflow.connect(node, out_file, z_score_std, 'inputspec.mask_file')
    except:
        logConnectionError('%s z-score standardize' % output_name, num_strat, \
                strat.get_resource_pool(), '0127',logger)
        raise
    strat.append_name(z_score_std.name)
    strat.update_resource_pool({'%s_zstd' % (output_resource):(z_score_std, \
                        'outputspec.z_score_img')},logger)
                
def fisher_z_score_standardize(workflow,output_name, output_resource, \
                timeseries_oned_file, strat, num_strat,logger, map_node=0):
    """
    """
    fisher_z_score_std = get_fisher_zscore(output_resource, map_node, \
                        'fisher_z_score_std_%s_%d' % (output_name, num_strat))
    try:
        node, out_file = strat.get_node_from_resource_pool(output_resource,logger)
        workflow.connect(node, out_file, fisher_z_score_std, 'inputspec.correlation_file')
        node, out_file = strat.get_node_from_resource_pool(timeseries_oned_file,logger)
        workflow.connect(node, out_file, fisher_z_score_std, 'inputspec.timeseries_one_d')
    except:
        logConnectionError('%s fisher z-score standardize' % output_name, num_strat, \
                strat.get_resource_pool(), '0128',logger)
        raise
    strat.append_name(fisher_z_score_std.name)
    strat.update_resource_pool({'%s_fisher_zstd' % (output_resource): \
            (fisher_z_score_std, 'outputspec.fisher_z_score_img')},logger) 

def connectCentralityWorkflow(methodOption,thresholdOption,threshold,\
                              weightOptions,mList,workflow,log_dir,num_strat):
    """
    """
    # Create centrality workflow
    network_centrality = create_resting_state_graphs(c.memoryAllocatedForDegreeCentrality,\
                         'network_centrality_%d-%d' %(num_strat,methodOption))
    # Connect registered function input image to inputspec
    workflow.connect(resample_functional_to_template, 'out_file',network_centrality, 'inputspec.subject')
    # Subject mask/parcellation image
    workflow.connect(template_dataflow, 'outputspec.out_file',network_centrality, 'inputspec.template')
    # Give which method we're doing (0 - deg, 1 - eig, 2 - lfcd)
    network_centrality.inputs.inputspec.method_option = methodOption
    # Type of threshold (0 - p-value, 1 - sparsity, 2 - corr)
    network_centrality.inputs.inputspec.threshold_option = thresholdOption
    # Connect threshold value (float)
    network_centrality.inputs.inputspec.threshold = threshold
    # List of two booleans, first for binary, second for weighted
    network_centrality.inputs.inputspec.weight_options = weightOptions
    # Merge output with others via merge_node connection
    workflow.connect(network_centrality,'outputspec.centrality_outputs',merge_node,mList)
    # Append this as a strategy
    strat.append_name(network_centrality.name)
    # Create log node for strategy
    create_log_node(workflow,network_centrality,'outputspec.centrality_outputs',num_strat,log_dir)
                        
def output_smooth_FuncToMNI(workflow,output_name,num_strat,logger,strat,log_dir,map_node=0):
    """
    """
    output_to_standard_smooth = None
    if map_node == 0:
        output_to_standard_smooth = pe.Node(interface= fsl.MultiImageMaths(), name='%s_to_standard_' \
                                    'smooth_%d' % (output_name, num_strat))
        output_to_standard_average = pe.Node(interface= preprocess.Maskave(), name='%s_to_standard_smooth_' \
                                    'mean_%d' % (output_name, num_strat))
        standard_mean_to_csv = pe.Node(util.Function( input_names=['in_file', 'output_name'], output_names=['output_mean'],\
                                    function=extract_output_mean),name='%s_to_standard_smooth_mean_to_txt_%d' % \
                                    (output_name, num_strat))
    elif map_node == 1:
        output_to_standard_smooth = pe.MapNode(interface= fsl.MultiImageMaths(), name='%s_to_standard_' \
                                    'smooth_%d' % (output_name, num_strat), iterfield=['in_file'])
        output_to_standard_average = pe.MapNode(interface= preprocess.Maskave(), name='%s_to_standard_smooth_' \
                                    'mean_%d' % (output_name, num_strat),iterfield=['in_file'])
        standard_mean_to_csv = pe.MapNode(util.Function( input_names=['in_file', 'output_name'], output_names=['output_mean'],
                                    function=extract_output_mean),name='%s_to_standard_smooth_mean_to_txt_%d' % \
                                    (output_name, num_strat), iterfield=['in_file'])

    standard_mean_to_csv.inputs.output_name = output_name + '_to_standard_smooth'
    try:
        node, out_file = strat.get_node_from_resource_pool('%s_to_standard'%output_name,logger)                
        workflow.connect(node, out_file, output_to_standard_smooth,'in_file')                
        workflow.connect(inputnode_fwhm, ('fwhm', set_gauss),output_to_standard_smooth, 'op_string')
        
        node, out_file = strat.get_node_from_resource_pool('functional_brain_mask_to_standard',logger)
        workflow.connect(node, out_file, output_to_standard_smooth,'operand_files')
        workflow.connect(output_to_standard_smooth, 'out_file',output_to_standard_average, 'in_file')
        workflow.connect(output_to_standard_average, 'out_file',standard_mean_to_csv, 'in_file')
    except:
        logConnectionError('%s smooth in MNI'%output_name,num_strat, strat.get_resource_pool(), '0028',logger)
        raise Exception
    
    strat.append_name(output_to_standard_smooth.name)
    strat.update_resource_pool({'%s_to_standard_smooth' % \
                    (output_name):(output_to_standard_smooth, 'out_file'),
                    'output_means.@%s_to_standard_smooth' % (output_name): \
                    (standard_mean_to_csv, 'output_mean')},logger)
    create_log_node(workflow,output_to_standard_smooth, 'out_file', num_strat,log_dir)
    
def output_smooth(workflow,inputnode_fwhm,output_name,output_resource,strat,\
                    num_strat,logger,c,log_dir,map_node=0):
    """
    """
    output_to_standard_smooth = None
    if map_node == 0:
        output_smooth = pe.Node(interface=fsl.MultiImageMaths(),name='%s_smooth_%d' % (output_name, num_strat))
        output_average = pe.Node(interface=preprocess.Maskave(),name='%s_smooth_mean_%d' % (output_name, num_strat))
        mean_to_csv = pe.Node(util.Function(input_names=['in_file', 'output_name'],\
                        output_names=['output_mean'],function=extract_output_mean),\
                        name='%s_smooth_mean_to_txt_%d' % (output_name,num_strat))

    elif map_node == 1:
        output_smooth = pe.MapNode(interface=fsl.MultiImageMaths(),\
                name='%s_smooth_%d' % (output_name, num_strat),iterfield=['in_file'])
        output_average = pe.MapNode(interface=preprocess.Maskave(),\
                name='%s_smooth_mean_%d' % (output_name, num_strat),iterfield=['in_file'])
        mean_to_csv = pe.MapNode(util.Function(input_names=['in_file', 'output_name'],\
                        output_names=['output_mean'],function=extract_output_mean),\
                        name='%s_smooth_mean_to_txt_%d' % (output_name,num_strat), iterfield=['in_file'])

    mean_to_csv.inputs.output_name = output_name + '_smooth'
    try:
        node, out_file = strat.get_node_from_resource_pool(output_resource,logger)
        workflow.connect(node, out_file, output_smooth, 'in_file')
        workflow.connect(inputnode_fwhm, ('fwhm', set_gauss), output_smooth, 'op_string')
        
        node, out_file = strat.get_node_from_resource_pool('functional_brain_mask',logger)
        workflow.connect(node, out_file, output_smooth, 'operand_files')
        workflow.connect(output_smooth, 'out_file', output_average,'in_file')
        workflow.connect(output_average, 'out_file', mean_to_csv,'in_file')
    except:
        logConnectionError('%s smooth' % output_name, num_strat,strat.get_resource_pool(), '0027',logger)
        raise

    strat.append_name(output_smooth.name)
    strat.update_resource_pool({'%s_smooth' % (output_name): \
            (output_smooth, 'out_file'),'output_means.@%s' % (output_name): (mean_to_csv, 'output_mean')},logger)

    if 1 in c.runRegisterFuncToMNI:
##        output_smooth_FuncToMNI(workflow,output_name, num_strat,logger,strat,log_dir,map_node)
        
        if map_node == 0:
            output_to_standard_smooth = pe.Node(interface= fsl.MultiImageMaths(), name='%s_to_standard_' \
                                        'smooth_%d' % (output_name, num_strat))
            output_to_standard_average = pe.Node(interface= preprocess.Maskave(), name='%s_to_standard_smooth_' \
                                        'mean_%d' % (output_name, num_strat))
            standard_mean_to_csv = pe.Node(util.Function( input_names=['in_file', 'output_name'], output_names=['output_mean'],\
                                        function=extract_output_mean),name='%s_to_standard_smooth_mean_to_txt_%d' % \
                                        (output_name, num_strat))
        elif map_node == 1:
            output_to_standard_smooth = pe.MapNode(interface= fsl.MultiImageMaths(), name='%s_to_standard_' \
                                        'smooth_%d' % (output_name, num_strat), iterfield=['in_file'])
            output_to_standard_average = pe.MapNode(interface= preprocess.Maskave(), name='%s_to_standard_smooth_' \
                                        'mean_%d' % (output_name, num_strat),iterfield=['in_file'])
            standard_mean_to_csv = pe.MapNode(util.Function( input_names=['in_file', 'output_name'], output_names=['output_mean'],
                                        function=extract_output_mean),name='%s_to_standard_smooth_mean_to_txt_%d' % \
                                        (output_name, num_strat), iterfield=['in_file'])

        standard_mean_to_csv.inputs.output_name = output_name + '_to_standard_smooth'
        try:
            node, out_file = strat.get_node_from_resource_pool('%s_to_standard'%output_name,logger)                
            workflow.connect(node, out_file, output_to_standard_smooth,'in_file')                
            workflow.connect(inputnode_fwhm, ('fwhm', set_gauss),output_to_standard_smooth, 'op_string')
            
            node, out_file = strat.get_node_from_resource_pool('functional_brain_mask_to_standard',logger)
            workflow.connect(node, out_file, output_to_standard_smooth,'operand_files')
            workflow.connect(output_to_standard_smooth, 'out_file',output_to_standard_average, 'in_file')
            workflow.connect(output_to_standard_average, 'out_file',standard_mean_to_csv, 'in_file')
        except:
            logConnectionError('%s smooth in MNI'%output_name,num_strat, strat.get_resource_pool(), '0028',logger)
            raise Exception
        
        strat.append_name(output_to_standard_smooth.name)
        strat.update_resource_pool({'%s_to_standard_smooth' % \
                        (output_name):(output_to_standard_smooth, 'out_file'),
                        'output_means.@%s_to_standard_smooth' % (output_name): \
                        (standard_mean_to_csv, 'output_mean')},logger)
        create_log_node(workflow,output_to_standard_smooth, 'out_file', num_strat,log_dir)  
    num_strat += 1
    return num_strat

def output_to_standard_FSL(c,workflow,logger,output_name, output_resource, strat, \
                                num_strat, map_node=0, input_image_type=0):
    """
    """
    if map_node == 0:
        apply_fsl_warp = pe.Node(interface=fsl.ApplyWarp(),\
                    name='%s_to_standard_%d' % (output_name, num_strat))       
    elif map_node == 1:
        apply_fsl_warp = pe.MapNode(interface=fsl.ApplyWarp(),\
                    name='%s_to_standard_%d' % (output_name, num_strat),\
                    iterfield=['in_file'])
    apply_fsl_warp.inputs.ref_file = c.template_skull_for_func

    try:
        # output file to be warped
        node, out_file = strat.get_node_from_resource_pool(output_resource,logger)
        workflow.connect(node, out_file, apply_fsl_warp, 'in_file')

        # linear affine from func->anat linear FLIRT registration
        node, out_file = strat.get_node_from_resource_pool('functional_to_anat_linear_xfm',logger)
        workflow.connect(node, out_file, apply_fsl_warp, 'premat')

        # nonlinear warp from anatomical->template FNIRT registration
        node, out_file = strat.get_node_from_resource_pool('anatomical_to_mni_nonlinear_xfm',logger)
        workflow.connect(node, out_file, apply_fsl_warp, 'field_file')
    except:
        logConnectionError('%s to MNI (FSL)' % (output_name),num_strat, \
                                strat.get_resource_pool(), '0021',logger)
        raise Exception
    strat.update_resource_pool(\
    {'%s_to_standard' % (output_name):(apply_fsl_warp, 'out_file')},logger)
    strat.append_name(apply_fsl_warp.name)
    num_strat += 1
    return num_strat

def connectNodes_to_standard_ANTS(workflow,strat,fsl_to_itk_convert,\
                        collect_transforms,apply_ants_warp, output_resource):
    """
    """
    # affine from FLIRT func->anat linear registration
    node, out_file = strat.get_node_from_resource_pool('functional_to_anat_linear_xfm',logger)
    workflow.connect(node, out_file, fsl_to_itk_convert,'inputspec.affine_file')

    # reference used in FLIRT func->anat linear registration
    node, out_file = strat.get_node_from_resource_pool('anatomical_brain',logger)
    workflow.connect(node, out_file, fsl_to_itk_convert,'inputspec.reference_file')

    # output file to be converted
    node, out_file = strat.get_node_from_resource_pool(output_resource,logger)
    workflow.connect(node, out_file, fsl_to_itk_convert,'inputspec.source_file')

    # nonlinear warp from anatomical->template ANTS registration
    node, out_file = strat.get_node_from_resource_pool('anatomical_to_mni_nonlinear_xfm',logger)
    workflow.connect(node, out_file, collect_transforms,'inputspec.warp_file')

    # linear initial from anatomical->template ANTS registration
    node, out_file = strat.get_node_from_resource_pool('ants_initial_xfm',logger)
    workflow.connect(node, out_file, collect_transforms,'inputspec.linear_initial')

    # linear affine from anatomical->template ANTS registration
    node, out_file = strat.get_node_from_resource_pool('ants_affine_xfm',logger)
    workflow.connect(node, out_file, collect_transforms,'inputspec.linear_affine')

    # rigid affine from anatomical->template ANTS registration
    node, out_file = strat.get_node_from_resource_pool('ants_rigid_xfm',logger)
    workflow.connect(node, out_file, collect_transforms,'inputspec.linear_rigid')

    # converted FLIRT func->anat affine, now in ITK (ANTS) format
    workflow.connect(fsl_to_itk_convert,'outputspec.itk_transform', \
                collect_transforms,'inputspec.fsl_to_itk_affine')

    # output file to be converted
    node, out_file = strat.get_node_from_resource_pool(output_resource,logger)
    workflow.connect(node, out_file, apply_ants_warp,'inputspec.input_image')

    # collection of warps to be applied to the output file
    workflow.connect(collect_transforms,'outputspec.transformation_series', \
                        apply_ants_warp,'inputspec.transforms')
    return workflow

def output_to_standard_ANTS(c,workflow,logger,output_name, output_resource, strat, \
                                num_strat, map_node=0, input_image_type=0):
    """
    """
    fsl_to_itk_convert = create_wf_c3d_fsl_to_itk(map_node, \
                    name= '%s_fsl_to_itk_%d' % (output_name, num_strat))
    collect_transforms = create_wf_collect_transforms(map_node,\
                    name='%s_collect_transforms_%d'% (output_name, num_strat))
    
    apply_ants_warp = create_wf_apply_ants_warp(map_node, \
                    name='%s_to_standard_%d' % (output_name, num_strat))
    apply_ants_warp.inputs.inputspec.dimension = 3
    apply_ants_warp.inputs.inputspec.interpolation = 'Linear'
    apply_ants_warp.inputs.inputspec.reference_image = c.template_brain_only_for_func
    apply_ants_warp.inputs.inputspec.input_image_type = input_image_type

    try:
        workflow = connectNodes_to_standard_ANTS(workflow,strat,\
                    fsl_to_itk_convert,collect_transforms,apply_ants_warp,\
                    output_resource)
    except:
        logConnectionError('%s to MNI (ANTS)' % (output_name),num_strat, \
                    strat.get_resource_pool(), '0022',logger)
        raise
    strat.update_resource_pool({'%s_to_standard' % (output_name): \
            (apply_ants_warp, 'outputspec.output_image')},logger)
    strat.append_name(apply_ants_warp.name)
    num_strat += 1
    return num_strat

def output_to_standard(c,workflow,logger,output_name, output_resource, strat, \
                        num_strat, map_node=0, input_image_type=0):
    """
    """
    nodes = getNodeList(strat)           
    if 'apply_ants_warp_functional_mni' in nodes:
        num_strat = output_to_standard_ANTS(c,workflow,logger,output_name,\
                    output_resource,strat,num_strat,map_node,input_image_type)
    else:
        num_strat = output_to_standard_FSL(c,workflow,logger,output_name, \
                output_resource, strat,num_strat, map_node, input_image_type)
    return num_strat
        
def setScanParamsInputSpecs(c,sub_dict,num_strat):
    """
    """
    scan_params = pe.Node(util.Function(\
                    input_names=['subject','scan','subject_map','start_indx','stop_indx'],
                    output_names=['tr','tpattern','ref_slice','start_indx','stop_indx'],
                    function=get_scan_params),
                    name='scan_params_%d' % num_strat)
    scan_params.inputs.subject_map = sub_dict
    scan_params.inputs.start_indx = c.startIdx
    scan_params.inputs.stop_indx = c.stopIdx                   
    return scan_params

def setConvertTrParams(num_strat):
    """
    """
    convert_tr = pe.Node(util.Function(input_names=['tr'],output_names=['tr'],\
                            function=get_tr),name='convert_tr_%d' % num_strat)
    convert_tr.inputs.tr = c.TR
    return convert_tr

def setFuncPreprocParams(c,func_preproc):
    """
    """
    func_preproc.inputs.inputspec.start_idx = c.startIdx
    func_preproc.inputs.inputspec.stop_idx = c.stopIdx
    return func_preproc

def runFristonModel(c,subject_id,sub_dict,workflow,workflow_bit_id,\
                    workflow_counter,strat_list,logger,log_dir):
    """
    """
    new_strat_list = []
    num_strat = 0
    if 1 in c.runFristonModel:
        workflow_bit_id['fristons_parameter_model'] = workflow_counter
        for strat in strat_list:
            fristons_model = fristons_twenty_four(\
                            wf_name='fristons_parameter_model_%d' % num_strat)
            try:
                node, out_file = strat.get_node_from_resource_pool(\
                                            'movement_parameters',logger)
                workflow.connect(node, out_file,
                                 fristons_model, 'inputspec.movement_file')
            except Exception as xxx:
                logConnectionError('Friston\'s Parameter Model', num_strat, \
                                strat.get_resource_pool(), '0006',logger)
                raise

            if 0 in c.runFristonModel:
                new_strat_list,strat=createNewStrategy(strat,new_strat_list)
            strat.append_name(fristons_model.name)
            strat.update_resource_pool({'movement_parameters':\
                        (fristons_model, 'outputspec.movement_file')},logger)    
            create_log_node(workflow,fristons_model, \
                                'outputspec.movement_file', num_strat,log_dir)            
            num_strat += 1            
    strat_list += new_strat_list
    return strat_list

def pick_wm(seg_prob_list):
    """
    """
    seg_prob_list.sort()
    return seg_prob_list[-1]

def runRegisterFuncToAnat(c,subject_id,sub_dict,workflow,workflow_bit_id,\
                                workflow_counter,strat_list,logger,log_dir):
    """
    """
    new_strat_list = []
    num_strat = 0
    if 1 in c.runRegisterFuncToAnat:
        workflow_bit_id['func_to_anat'] = workflow_counter
        for strat in strat_list:
            func_to_anat = create_register_func_to_anat('func_to_anat_FLIRT_%d'\
                                % num_strat)
            func_to_anat.inputs.inputspec.interp = 'trilinear'
            try:
                node, out_file = strat.get_node_from_resource_pool('mean_functional',logger)
                workflow.connect(node, out_file,func_to_anat, 'inputspec.func')
                node, out_file = strat.get_node_from_resource_pool('anatomical_brain',logger)
                workflow.connect(node, out_file,func_to_anat, 'inputspec.anat')
            except:               
                logConnectionError('Register Functional to Anatomical (pre BBReg)', \
                                   num_strat, strat.get_resource_pool(), '0007',logger)
                raise
            if 0 in c.runRegisterFuncToAnat:
                new_strat_list,strat=createNewStrategy(strat,new_strat_list)
            strat.append_name(func_to_anat.name)
            strat.update_resource_pool({'mean_functional_in_anat':\
                        (func_to_anat, 'outputspec.anat_func_nobbreg'),
                                        'functional_to_anat_linear_xfm':\
                        (func_to_anat, 'outputspec.func_to_anat_linear_xfm_nobbreg')},logger)
            num_strat += 1
    strat_list += new_strat_list
    return strat_list
    
def runGenerateMotionStatistics(c,subject_id,sub_dict,workflow,\
                    workflow_bit_id,workflow_counter,strat_list,logger,\
                    log_dir,funcFlow):
    """
    """
    new_strat_list = []
    num_strat = 0
    if 1 in c.runGenerateMotionStatistics:
        workflow_bit_id['gen_motion_stats'] = workflow_counter
        for strat in strat_list:
            gen_motion_stats = motion_power_statistics('gen_motion_stats_%d' % num_strat)
            gen_motion_stats = setGenMotionStatsParams(c,gen_motion_stats)

            try:
                # #**special case where the workflow is not getting outputs from resource pool
                # but is connected to functional datasource
                workflow.connect(funcFlow, 'outputspec.subject',gen_motion_stats, 'inputspec.subject_id')
                workflow.connect(funcFlow, 'outputspec.scan',gen_motion_stats, 'inputspec.scan_id')

                node, out_file = strat.get_node_from_resource_pool('motion_correct',logger)
                workflow.connect(node, out_file,gen_motion_stats, 'inputspec.motion_correct')

                node, out_file = strat.get_node_from_resource_pool('movement_parameters',logger)
                workflow.connect(node, out_file,gen_motion_stats, 'inputspec.movement_parameters')

                node, out_file = strat.get_node_from_resource_pool('max_displacement',logger)
                workflow.connect(node, out_file,gen_motion_stats, 'inputspec.max_displacement')

                node, out_file = strat.get_node_from_resource_pool('functional_brain_mask',logger)
                workflow.connect(node, out_file,gen_motion_stats, 'inputspec.mask')

                node, out_file = strat.get_node_from_resource_pool('coordinate_transformation',logger)
                workflow.connect(node, out_file,gen_motion_stats, 'inputspec.oned_matrix_save')
            except:
                logConnectionError('Generate Motion Statistics',num_strat, \
                                    strat.get_resource_pool(), '0009',logger)
                raise

            if 0 in c.runGenerateMotionStatistics:
                new_strat_list,strat=createNewStrategy(strat,new_strat_list)
            strat.append_name(gen_motion_stats.name)
            strat.update_resource_pool({'frame_wise_displacement':(gen_motion_stats, 'outputspec.FD_1D'),
                                        'scrubbing_frames_excluded':(gen_motion_stats, 'outputspec.frames_ex_1D'),
                                        'scrubbing_frames_included':(gen_motion_stats, 'outputspec.frames_in_1D'),
                                        'power_params':(gen_motion_stats, 'outputspec.power_params'),
                                        'motion_params':(gen_motion_stats, 'outputspec.motion_params')},logger)            
            create_log_node(workflow,gen_motion_stats, 'outputspec.motion_params', num_strat,log_dir)
            num_strat += 1
    strat_list += new_strat_list
    return strat_list

def setGenMotionStatsParams(c,gen_motion_stats):
    """
    """
    gen_motion_stats.inputs.scrubbing_input.threshold = c.scrubbingThreshold
    gen_motion_stats.inputs.scrubbing_input.remove_frames_before = c.numRemovePrecedingFrames
    gen_motion_stats.inputs.scrubbing_input.remove_frames_after = c.numRemoveSubsequentFrames
    gen_motion_stats.get_node('scrubbing_input').iterables = ('threshold', c.scrubbingThreshold)
    return gen_motion_stats
    



##def functionalMasking3dAutoMask(slice_timing,workflow,scan_params,convert_tr,\
##                                num_strat):
##    """
##    """
##    if slice_timing:
##        func_preproc = create_func_preproc(slice_timing_correction=True, \
##                                           use_bet=False, \
##                                           wf_name='func_preproc_automask_%d' % num_strat)
##        workflow.connect(funcFlow, 'outputspec.subject',
##                         scan_params, 'subject')
##        workflow.connect(funcFlow, 'outputspec.scan',
##                         scan_params, 'scan')                   
##        workflow.connect(scan_params, 'tr',
##                         func_preproc, 'scan_params.tr')
##        workflow.connect(scan_params, 'ref_slice',
##                         func_preproc, 'scan_params.ref_slice')
##        workflow.connect(scan_params, 'tpattern',
##                         func_preproc, 'scan_params.acquisition')
##        workflow.connect(scan_params, 'start_indx',
##                         func_preproc, 'inputspec.start_idx')
##        workflow.connect(scan_params, 'stop_indx',
##                         func_preproc, 'inputspec.stop_idx')
##        workflow.connect(scan_params, 'tr',
##                         convert_tr, 'tr')
##    else:
##        func_preproc = create_func_preproc(slice_timing_correction=False, \
##                    wf_name='func_preproc_automask_%d' % num_strat)
##        func_preproc = setFuncPreprocParams(c,func_preproc)
##    try:
##        node, out_file = strat.get_leaf_properties()
##        workflow.connect(node, out_file, func_preproc, 'inputspec.rest')
##    except:
##        logConnectionError('Functional Preprocessing', \
##                           num_strat, strat.get_resource_pool(), '0005',logger)
##        num_strat += 1
##        continue
##       
##    return num_strat    
##    
##    
##def runFunctionalPreprocessing(c,subject_id,sub_dict,workflow,\
##                                workflow_bit_id,workflow_counter,\
##                                strat_list,logger,log_dir):
##    """
##    """
##    new_strat_list = []
##    num_strat = 0
##    if 1 in c.runFunctionalPreprocessing:
##        workflow_bit_id['func_preproc'] = workflow_counter
##        slice_timing = sub_dict.get('scan_parameters')
##        scan_params = setScanParamsInputSpecs(c,sub_dict,num_strat)
##        convert_tr = setConvertTrParams(num_strat) 
##        for strat in strat_list:            
##            if '3dAutoMask' in c.functionalMasking:  
##                num_strat = functionalMasking3dAutoMask(slice_timing,\
##                                                        workflow,scan_params)               
##                
##                if (0 in c.runFunctionalPreprocessing) or ('BET' in c.functionalMasking):
##        new_strat_list,strat=createNewStrategy(strat,new_strat_list)
##    strat.append_name(func_preproc.name)
##    strat.set_leaf_properties(func_preproc, 'outputspec.preprocessed')
##
##    # add stuff to resource pool if we need it
##    if slice_timing:
##        strat.update_resource_pool({'slice_timing_corrected': (func_preproc, 'outputspec.slice_time_corrected')},logger)
##    strat.update_resource_pool({'mean_functional':(func_preproc, 'outputspec.example_func')},logger)
##    strat.update_resource_pool({'functional_preprocessed_mask':(func_preproc, 'outputspec.preprocessed_mask')},logger)
##    strat.update_resource_pool({'movement_parameters':(func_preproc, 'outputspec.movement_parameters')},logger)
##    strat.update_resource_pool({'max_displacement':(func_preproc, 'outputspec.max_displacement')},logger)
##    strat.update_resource_pool({'preprocessed':(func_preproc, 'outputspec.preprocessed')},logger)
##    strat.update_resource_pool({'functional_brain_mask':(func_preproc, 'outputspec.mask')},logger)
##    strat.update_resource_pool({'motion_correct':(func_preproc, 'outputspec.motion_correct')},logger)
##    strat.update_resource_pool({'coordinate_transformation':(func_preproc, 'outputspec.oned_matrix_save')},logger)
##
##    create_log_node(workflow,func_preproc, 'outputspec.preprocessed', num_strat,log_dir)
##    num_strat += 1
##        strat_list += new_strat_list
##        new_strat_list = []
##            
##
##        for strat in strat_list:            
##            nodes = getNodeList(strat)            
##            if ('BET' in c.functionalMasking) and ('func_preproc_automask' not in nodes):
##
##                slice_timing = sub_dict.get('scan_parameters')
##                # a node which checks if scan _parameters are present for each scan
##                scan_params = pe.Node(util.Function(input_names=['subject',
##                                                                 'scan',
##                                                                 'subject_map',
##                                                                 'start_indx',
##                                                                 'stop_indx'],
##                                                   output_names=['tr',
##                                                                 'tpattern',
##                                                                 'ref_slice',
##                                                                 'start_indx',
##                                                                 'stop_indx'],
##                                                   function=get_scan_params),
##                                     name='scan_params_%d' % num_strat)
##
##                convert_tr = pe.Node(util.Function(input_names=['tr'],
##                                                   output_names=['tr'],
##                                                   function=get_tr),
##                                     name='convert_tr_%d' % num_strat)
##
##                # if scan parameters are available slice timing correction is
##                # turned on
##                if slice_timing:
##
##                    func_preproc = create_func_preproc(slice_timing_correction=True, \
##                                                       use_bet=True, \
##                                                       wf_name='func_preproc_bet_%d' % num_strat)
##
##                    # getting the scan parameters
##                    workflow.connect(funcFlow, 'outputspec.subject',
##                                     scan_params, 'subject')
##                    workflow.connect(funcFlow, 'outputspec.scan',
##                                     scan_params, 'scan')
##                    scan_params.inputs.subject_map = sub_dict
##                    scan_params.inputs.start_indx = c.startIdx
##                    scan_params.inputs.stop_indx = c.stopIdx
##
##                    # passing the slice timing correction parameters
##                    workflow.connect(scan_params, 'tr',
##                                     func_preproc, 'scan_params.tr')
##                    workflow.connect(scan_params, 'ref_slice',
##                                     func_preproc, 'scan_params.ref_slice')
##                    workflow.connect(scan_params, 'tpattern',
##                                     func_preproc, 'scan_params.acquisition')
##
##                    workflow.connect(scan_params, 'start_indx',
##                                     func_preproc, 'inputspec.start_idx')
##                    workflow.connect(scan_params, 'stop_indx',
##                                     func_preproc, 'inputspec.stop_idx')
##
##                    workflow.connect(scan_params, 'tr',
##                                     convert_tr, 'tr')
##                else:
##                    func_preproc = create_func_preproc(slice_timing_correction=False, \
##                                                       use_bet=True, \
##                                                       wf_name='func_preproc_bet_%d' % num_strat)
##                    func_preproc.inputs.inputspec.start_idx = c.startIdx
##                    func_preproc.inputs.inputspec.stop_idx = c.stopIdx
##
##                    convert_tr.inputs.tr = c.TR
##
##                node = None
##                out_file = None
##                try:
##                    node, out_file = strat.get_leaf_properties()
##                    workflow.connect(node, out_file, func_preproc, 'inputspec.rest')
##
##                except:
##                    logConnectionError('Functional Preprocessing', \
##                                       num_strat, strat.get_resource_pool(), '0005',logger)
##                    num_strat += 1
##                    continue
##
##                if 0 in c.runFunctionalPreprocessing:
##                    new_strat_list,strat=createNewStrategy(strat,new_strat_list)
##
##                strat.append_name(func_preproc.name)
##                strat.set_leaf_properties(func_preproc, 'outputspec.preprocessed')
##
##                # add stuff to resource pool if we need it
##                if slice_timing:
##                    strat.update_resource_pool({'slice_timing_corrected': (func_preproc, 'outputspec.slice_time_corrected')},logger)
##                strat.update_resource_pool({'mean_functional':(func_preproc, 'outputspec.example_func')},logger)
##                strat.update_resource_pool({'functional_preprocessed_mask':(func_preproc, 'outputspec.preprocessed_mask')},logger)
##                strat.update_resource_pool({'movement_parameters':(func_preproc, 'outputspec.movement_parameters')},logger)
##                strat.update_resource_pool({'max_displacement':(func_preproc, 'outputspec.max_displacement')},logger)
##                strat.update_resource_pool({'preprocessed':(func_preproc, 'outputspec.preprocessed')},logger)
##                strat.update_resource_pool({'functional_brain_mask':(func_preproc, 'outputspec.mask')},logger)
##                strat.update_resource_pool({'motion_correct':(func_preproc, 'outputspec.motion_correct')},logger)
##                strat.update_resource_pool({'coordinate_transformation':(func_preproc, 'outputspec.oned_matrix_save')},logger)
##
##
##                create_log_node(workflow,func_preproc, 'outputspec.preprocessed', num_strat,log_dir)
##                num_strat += 1
##
##
##    strat_list += new_strat_list
##    return strat_list

























    
    
    
    
    
    
    
    
    
    
    
