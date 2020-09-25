''' example rtl tasklet '''
import dace

import numpy as np

# add sdfg
sdfg = dace.SDFG('rtl_tasklet_demo')

# add state
state = sdfg.add_state()

# add arrays
sdfg.add_array('A', [1], dtype=dace.int32)
sdfg.add_array('B', [1], dtype=dace.int32)

# add custom cpp tasklet
tasklet = state.add_tasklet(
    name='rtl_tasklet',
    inputs={'a'},
    outputs={'b'},
    code='''
    /*
        Convention:
        - clk_i is the global clock
        - rst_i is the reset input (rst on high) 
        - valid_o signals valid output data (end of simulation)
    */
    
    always@(posedge clk_i) begin
        if (rst_i)
            b <= a;
        else
            b <= b + 1;
    end    
      
    assign valid_o = (b >= 100) ? 1'b1:1'b0;
    ''',
    language=dace.Language.RTL)

# add input/output array
A = state.add_read('A')
B = state.add_write('B')

# connect input/output array with the tasklet
state.add_edge(A, None, tasklet, 'a', dace.Memlet.simple('A', '0'))
state.add_edge(tasklet, 'b', B, None, dace.Memlet.simple('B', '0'))

# validate sdfg
sdfg.validate()

######################################################################


if __name__ == '__main__':

    # init data structures
    a = np.random.randint(0, 100, 1).astype(np.int32)
    b = np.random.randint(0, 100, 1).astype(np.int32)

    # show initial values
    print("a={}, b={}".format(a, b))

    # call program
    sdfg(A=a, B=b)

    # show result
    print("a={}, b={}".format(a, b))
