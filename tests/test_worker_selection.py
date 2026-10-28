from taskplan.worker_selection import SelectionPolicy, repository_rejection, priority_key
import pytest

@pytest.mark.parametrize('task',[{}, {'assigned_to':'USER'}, {'assigned_to':'claude'},
    {'assigned_slot':'someone-else','required_model':'other-model','assigned_to':'openrouter'}])
def test_default_routing_is_preference(task):
    assert SelectionPolicy().rejection(task,{'id':'agent','model':'model'}) is None

def test_task_only_and_explicit_model_setting_are_distinct():
    task={'assigned_slot':'agent','required_model':'model'}
    assert SelectionPolicy(True).rejection(task,{'id':'other','model':'model'})=='slot_binding'
    assert SelectionPolicy(False,True).rejection(task,{'id':'other','model':'other'})=='model_binding'
    assert SelectionPolicy(True,True).rejection(task,{'id':'agent','model':'model'}) is None
    assert SelectionPolicy(True).rejection({}, {'id':'agent'})=='explicit_slot_required'

def test_override_inheritance_and_worker_restriction():
    assert SelectionPolicy.merge({'require_assigned_slot':True},{'require_assigned_slot':False}).require_assigned_slot is False
    assert SelectionPolicy.merge({},profile={'require_assigned_slot':True}).require_assigned_slot is True
    with pytest.raises(ValueError):SelectionPolicy(require_assigned_slot=1)

def test_optional_repository_list_and_explicit_restriction():
    assert repository_rejection('org/repo',[]) is None
    assert repository_rejection('org/repo',['org/repo']) is None
    assert repository_rejection('org/other',['org/repo'])=='repository_not_allowed'
    with pytest.raises(ValueError):repository_rejection('org/repo',None)

def test_priorities_order_without_exclusion():
    rows=[{'id':i+1,'priority':p} for i,p in enumerate(['P3','P1','P4','P0','P2'])]
    assert [t['priority'] for t in sorted(rows,key=priority_key)]==['P0','P1','P2','P3','P4']
